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
from common.infrastructure.resilience import sanitize_error_message
from mcp.telemetry_mcp.application.ports import TelemetryRepositoryPort
from mcp.telemetry_mcp.application.use_cases import (
    DiagnoseFaultCodeUseCase,
    GetActiveAlertsUseCase,
    GetLatestMetricsUseCase,
    QueryRangeUseCase,
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


def register_tools(app: FastMCP, settings: Settings, repo: Optional[TelemetryRepositoryPort] = None) -> None:
    """Register all Telemetry MCP tools onto the FastMCP server instance.

    Args:
        app: The FastMCP server instance.
        settings: Application settings (used for source label in responses).
        repo: Pre-constructed, server-scoped telemetry repository. If omitted,
              the singleton gateway instance from GatewayFactory is used.
    """
    if repo is None:
        from core.factory import GatewayFactory
        repo = GatewayFactory.get_telemetry_gateway(settings)

    source_name = settings.DATA_PROVIDER_MODE.lower()

    @app.tool()
    async def telemetry_get_latest_metrics(
        machine_id: str,
        metric_names: Optional[str] = None,
    ) -> dict[str, Any]:
        """Returns the most recent snapshot of operational sensor values with engineering units.

        Args:
            machine_id: Machine identifier in MCH-XXXX-XXXX format (e.g., MCH-A1B2-C3D4).
            metric_names: Optional comma-separated list of metrics (vibration, temperature, rpm, pressure, power_kw, humidity, current_amp, voltage, max 200 chars). Leave empty for all sensors.
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="telemetry_get_latest_metrics", machine_id=machine_id)
        with trace_mcp_tool("telemetry_get_latest_metrics", "telemetry_mcp", correlation_id, machine_id=machine_id):
            try:
                # SEC-04: Length limits
                if metric_names and len(metric_names) > 200:
                    raise DomainValidationError("metric_names length cannot exceed 200 characters")
                logger.info("Handling telemetry_get_latest_metrics", machine_id=machine_id, metric_names=metric_names)
                metrics_list = (
                    [m.strip().lower() for m in metric_names.split(",") if m.strip()]
                    if metric_names
                    else None
                )
                snapshot = await GetLatestMetricsUseCase(repo).execute(machine_id, metrics_list)
                return _success_response(snapshot, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed telemetry_get_latest_metrics", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def telemetry_query_range(
        machine_id: str,
        start_time: str,
        end_time: str,
        resolution: str = "5m",
        aggregation: str = "AVG",
    ) -> dict[str, Any]:
        """Queries historical time-series sensor buckets for operational analytics.

        Args:
            machine_id: Machine identifier in MCH-XXXX-XXXX format (e.g., MCH-A1B2-C3D4).
            start_time: ISO-8601 start timestamp (e.g., 2026-11-17T08:00:00Z).
            end_time: ISO-8601 end timestamp (e.g., 2026-11-17T12:00:00Z).
            resolution: Bucket size. Allowed: 1m, 5m, 1h, 6h, 1d (default: 5m).
            aggregation: Aggregation function. Allowed: AVG, MAX, MIN (default: AVG).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="telemetry_query_range", machine_id=machine_id)
        with trace_mcp_tool("telemetry_query_range", "telemetry_mcp", correlation_id, machine_id=machine_id):
            try:
                logger.info(
                    "Handling telemetry_query_range",
                    machine_id=machine_id,
                    start_time=start_time,
                    end_time=end_time,
                    resolution=resolution,
                    aggregation=aggregation,
                )
                try:
                    s_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    e_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise DomainValidationError(f"Invalid timestamp format: {exc}. Use ISO-8601 format.") from exc

                results = await QueryRangeUseCase(repo).execute(machine_id, s_dt, e_dt, resolution, aggregation)
                return _success_response(
                    {"series": results, "series_count": len(results)},
                    source_name,
                    correlation_id,
                )
            except Exception as exc:
                logger.error("Failed telemetry_query_range", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def telemetry_get_active_alerts(
        machine_id: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetches active threshold-breach alarms, out-of-spec warnings, and emergency condition flags.

        Args:
            machine_id: Optional machine filter in MCH-XXXX-XXXX format.
            severity: Filter by severity level (INFO, WARNING, CRITICAL).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="telemetry_get_active_alerts", machine_id=machine_id, severity=severity)
        with trace_mcp_tool("telemetry_get_active_alerts", "telemetry_mcp", correlation_id, machine_id=machine_id, severity=severity):
            try:
                logger.info("Handling telemetry_get_active_alerts", machine_id=machine_id, severity=severity)
                alerts = await GetActiveAlertsUseCase(repo).execute(machine_id, severity)
                return _success_response(
                    {"alerts": alerts, "total_active": len(alerts)},
                    source_name,
                    correlation_id,
                )
            except Exception as exc:
                logger.error("Failed telemetry_get_active_alerts", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()

    @app.tool()
    async def telemetry_diagnose_fault_code(fault_code: str) -> dict[str, Any]:
        """Provides expert diagnostic procedures, root causes, and recommended repair parts for diagnostic trouble codes.

        Args:
            fault_code: Equipment diagnostic trouble code (e.g., E-001, V-002, T-003, max 50 chars).
        """
        correlation_id = str(uuid.uuid4())
        bind_context(correlation_id=correlation_id, tool_name="telemetry_diagnose_fault_code", fault_code=fault_code)
        with trace_mcp_tool("telemetry_diagnose_fault_code", "telemetry_mcp", correlation_id, fault_code=fault_code):
            try:
                # SEC-04: Length limits
                if len(fault_code) > 50:
                    raise DomainValidationError("fault_code length cannot exceed 50 characters")
                logger.info("Handling telemetry_diagnose_fault_code", fault_code=fault_code)
                diagnosis = await DiagnoseFaultCodeUseCase(repo).execute(fault_code)
                return _success_response(diagnosis, source_name, correlation_id)
            except Exception as exc:
                logger.error("Failed telemetry_diagnose_fault_code", error=str(exc))
                return _error_response(exc, correlation_id)
            finally:
                clear_context()
