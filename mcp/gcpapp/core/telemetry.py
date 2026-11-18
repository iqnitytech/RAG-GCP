from __future__ import annotations
import uuid
from contextlib import contextmanager
from typing import Any, Generator, Optional

try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Status, StatusCode
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None  # type: ignore[assignment]
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]

from core.logging import get_logger

logger = get_logger(__name__)


def get_tracer(service_name: str = "mcp-platform") -> Any:
    """Obtain an OpenTelemetry tracer instance."""
    if OTEL_AVAILABLE and trace is not None:
        return trace.get_tracer(service_name)
    return None


def get_current_trace_id() -> str:
    """Return the active OpenTelemetry trace ID as a 32-char hex string,
    or generate a fallback UUID if not active (OBS-01).
    """
    if OTEL_AVAILABLE and trace is not None:
        current_span = trace.get_current_span()
        if current_span is not None and current_span.is_recording():
            ctx = current_span.get_span_context()
            if ctx.is_valid:
                return f"{ctx.trace_id:032x}"
    return uuid.uuid4().hex


@contextmanager
def trace_mcp_tool(
    tool_name: str,
    server_name: str,
    correlation_id: str,
    **extra_attrs: Any,
) -> Generator[Optional[Any], None, None]:
    """Context manager creating an OpenTelemetry span for MCP tool executions (OBS-01).
    Tracks tool boundary, execution status, latency, correlation ID, and exceptions.
    """
    tracer = get_tracer(f"mcp.{server_name}")
    if tracer is None:
        yield None
        return

    span_name = f"mcp.tool.{tool_name}"
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("mcp.tool.name", tool_name)
        span.set_attribute("mcp.server.name", server_name)
        span.set_attribute("mcp.correlation_id", correlation_id)
        for k, v in extra_attrs.items():
            if v is not None:
                span.set_attribute(f"mcp.arg.{k}", str(v))

        try:
            yield span
            if StatusCode is not None and Status is not None:
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.record_exception(exc)
            if StatusCode is not None and Status is not None:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
