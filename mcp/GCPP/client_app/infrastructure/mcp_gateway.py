from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from typing import Any

from contextlib import nullcontext
from client_app.application.ports import McpClientGatewayPort
from client_app.domain.entities import (
    ServerDescriptor,
    ToolDescriptor,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolParameter,
)
from client_app.domain.exceptions import ServerNotFoundError, ToolNotFoundError
from config.settings import Settings
from core.telemetry import get_tracer

PRESET_ARGUMENTS: dict[str, dict[str, Any]] = {
    "customer_get_profile": {"customer_id": "CUST-001001"},
    "customer_search": {
        "query": "Nexus",
        "region": "NA-EAST",
        "tier": "ENTERPRISE",
        "limit": 10,
        "offset": 0,
    },
    "customer_get_entitlements": {"customer_id": "CUST-001001"},
    "customer_get_assigned_machines": {"customer_id": "CUST-001001", "site_id": "SITE-A1B2"},
    "machine_get_details": {"machine_id": "MCH-A1B2-C3D4"},
    "machine_list_fleet": {
        "model_type": "",
        "status": "ACTIVE",
        "site_id": "",
        "page": 1,
        "page_size": 20,
    },
    "machine_get_maintenance_history": {"machine_id": "MCH-A1B2-C3D4", "limit": 10},
    "machine_update_operational_state": {
        "machine_id": "MCH-A1B2-C3D4",
        "target_state": "MAINTENANCE",
        "reason": "Routine diagnostics overhaul",
    },
    "telemetry_get_latest_metrics": {
        "machine_id": "MCH-A1B2-C3D4",
        "metric_names": "vibration,temperature,rpm",
    },
    "telemetry_query_range": {
        "machine_id": "MCH-A1B2-C3D4",
        "start_time": "2026-11-17T08:00:00Z",
        "end_time": "2026-11-17T12:00:00Z",
        "resolution": "5m",
        "aggregation": "AVG",
    },
    "telemetry_get_active_alerts": {
        "machine_id": "MCH-X9Y8-Z7W6",
        "severity": "CRITICAL",
    },
    "telemetry_diagnose_fault_code": {"fault_code": "E-001"},
}


class LocalMcpClientGateway(McpClientGatewayPort):
    """Gateway that connects to and coordinates the three MCP server instances."""

    def __init__(self) -> None:
        self._server_cache: dict[str, Any] = {}

    def _get_server(self, server_id: str, mode: str = "MOCK") -> Any:
        mode_upper = mode.upper()
        cache_key = f"{server_id.lower()}:{mode_upper}"
        if cache_key in self._server_cache:
            return self._server_cache[cache_key]

        from config.settings import Settings
        settings = Settings(DATA_PROVIDER_MODE=mode_upper)

        if server_id == "customer":
            from mcp.customer_mcp.server import create_server
            server = create_server(settings=settings)
        elif server_id == "machine":
            from mcp.machine_mcp.server import create_server
            server = create_server(settings=settings)
        elif server_id == "telemetry":
            from mcp.telemetry_mcp.server import create_server
            server = create_server(settings=settings)
        else:
            raise ServerNotFoundError(f"Unknown server_id '{server_id}'. Valid: customer, machine, telemetry")

        self._server_cache[cache_key] = server
        return server

    async def list_servers(self) -> list[ServerDescriptor]:
        servers_meta = [
            ("customer", "Customer MCP Server", "Enterprise 360 Customer Profiles, SLAs & Fleet Associations"),
            ("machine", "Machine MCP Server", "Industrial Equipment Hardware Specs, Fleet Registry & Maintenance Logs"),
            ("telemetry", "Telemetry MCP Server", "Operational Sensor Snapshots, Time-Series & Anomaly Diagnostics"),
        ]

        descriptors: list[ServerDescriptor] = []
        for s_id, d_name, desc in servers_meta:
            app = self._get_server(s_id, mode="MOCK")
            raw_tools = await app.list_tools()
            tool_descriptors: list[ToolDescriptor] = []

            for t in raw_tools:
                params: list[ToolParameter] = []
                schema = (
                    getattr(t, "input_schema", None)
                    or getattr(t, "inputSchema", None)
                    or {}
                )
                if hasattr(schema, "model_dump"):
                    schema = schema.model_dump()
                elif hasattr(schema, "dict"):
                    schema = schema.dict()

                properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
                required_fields = set(schema.get("required", [])) if isinstance(schema, dict) else set()

                for p_name, p_spec in properties.items():
                    p_type = p_spec.get("type")
                    if not p_type and "anyOf" in p_spec:
                        for sub in p_spec["anyOf"]:
                            if sub.get("type") and sub.get("type") != "null":
                                p_type = sub.get("type")
                                break
                    if not p_type:
                        p_type = "string"

                    params.append(
                        ToolParameter(
                            name=p_name,
                            type_name=p_type,
                            required=p_name in required_fields,
                            description=p_spec.get("description", p_spec.get("title", "")),
                            default=p_spec.get("default"),
                            example=PRESET_ARGUMENTS.get(t.name, {}).get(p_name),
                        )
                    )

                tool_descriptors.append(
                    ToolDescriptor(
                        server_id=s_id,
                        name=t.name,
                        description=t.description or "",
                        parameters=params,
                        preset_arguments=PRESET_ARGUMENTS.get(t.name, {}),
                    )
                )

            descriptors.append(
                ServerDescriptor(
                    server_id=s_id,
                    display_name=d_name,
                    description=desc,
                    tools=tool_descriptors,
                )
            )

        return descriptors

    async def get_tool(self, server_id: str, tool_name: str) -> ToolDescriptor:
        servers = await self.list_servers()
        for s in servers:
            if s.server_id == server_id:
                for t in s.tools:
                    if t.name == tool_name:
                        return t
        raise ToolNotFoundError(f"Tool '{tool_name}' not found on server '{server_id}'")

    async def invoke_tool(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        tracer = get_tracer("mcp.client_gateway")
        cm = tracer.start_as_current_span(f"mcp.invoke.{request.tool_name}") if tracer else nullcontext()
        with cm as span:
            if span and hasattr(span, "set_attribute"):
                span.set_attribute("mcp.server_id", request.server_id)
                span.set_attribute("mcp.tool_name", request.tool_name)
                span.set_attribute("mcp.mode", request.mode)

            start_time = time.perf_counter()
            now_iso = datetime.now(timezone.utc).isoformat()

            try:
                app = self._get_server(request.server_id, mode=request.mode)
                call_result = await app.call_tool(request.tool_name, request.arguments)
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

                payload: dict[str, Any] = {}
                if hasattr(call_result, "content"):
                    for item in call_result.content:
                        if hasattr(item, "text"):
                            try:
                                payload = json.loads(item.text)
                            except Exception:
                                payload = {"raw_text": item.text}
                        else:
                            payload = {"raw_item": str(item)}
                elif isinstance(call_result, dict):
                    payload = call_result
                else:
                    payload = {"result": str(call_result)}

                is_error = getattr(call_result, "is_error", getattr(call_result, "isError", False))
                is_success = payload.get("status") == "success" or not (
                    is_error or payload.get("status") == "error"
                )

                if span and hasattr(span, "set_attribute"):
                    span.set_attribute("mcp.status", "success" if is_success else "error")

                return ToolInvocationResult(
                    server_id=request.server_id,
                    tool_name=request.tool_name,
                    status="success" if is_success else "error",
                    payload=payload,
                    duration_ms=duration_ms,
                    timestamp=now_iso,
                )
            except Exception as exc:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                if span and hasattr(span, "record_exception"):
                    span.record_exception(exc)
                return ToolInvocationResult(
                    server_id=request.server_id,
                    tool_name=request.tool_name,
                    status="error",
                    payload={
                        "status": "error",
                        "data": {"message": str(exc)},
                        "metadata": {"source": "client_gateway", "timestamp": now_iso},
                    },
                    duration_ms=duration_ms,
                    timestamp=now_iso,
                    raw_error=str(exc),
                )
