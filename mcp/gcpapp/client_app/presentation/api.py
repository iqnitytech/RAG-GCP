from __future__ import annotations
import dataclasses
from typing import Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from client_app.application.use_cases import (
    PREDEFINED_SCENARIOS,
    ExecuteScenarioUseCase,
    GetServersAndToolsUseCase,
    InvokeToolUseCase,
)
from client_app.domain.entities import ToolInvocationRequest
from client_app.infrastructure.mcp_gateway import LocalMcpClientGateway

router = APIRouter(prefix="/api")
gateway = LocalMcpClientGateway()


class InvokePayload(BaseModel):
    server_id: str = Field(..., json_schema_extra={"example": "customer"})
    tool_name: str = Field(..., json_schema_extra={"example": "customer_get_profile"})
    arguments: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"example": {"customer_id": "CUST-001001"}})
    mode: str = Field(default="MOCK", json_schema_extra={"example": "MOCK"})


class ScenarioRunPayload(BaseModel):
    mode: str = Field(default="MOCK", json_schema_extra={"example": "MOCK"})


@router.get("/servers")
async def list_servers() -> list[dict[str, Any]]:
    use_case = GetServersAndToolsUseCase(gateway)
    servers = await use_case.execute()
    return [dataclasses.asdict(s) for s in servers]


@router.post("/invoke")
async def invoke_tool(payload: InvokePayload) -> dict[str, Any]:
    try:
        use_case = InvokeToolUseCase(gateway)
        req = ToolInvocationRequest(
            server_id=payload.server_id,
            tool_name=payload.tool_name,
            arguments=payload.arguments,
            mode=payload.mode,
        )
        res = await use_case.execute(req)
        return dataclasses.asdict(res)
    except Exception as exc:
        from datetime import datetime, timezone
        return {
            "server_id": payload.server_id,
            "tool_name": payload.tool_name,
            "status": "error",
            "payload": {
                "status": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            "duration_ms": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "raw_error": str(exc),
        }


@router.get("/scenarios")
async def list_scenarios() -> list[dict[str, Any]]:
    return list(PREDEFINED_SCENARIOS.values())


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(scenario_id: str, payload: ScenarioRunPayload) -> dict[str, Any]:
    use_case = ExecuteScenarioUseCase(gateway)
    try:
        result = await use_case.execute(scenario_id, mode=payload.mode)
        return dataclasses.asdict(result)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/health")
async def api_health() -> dict[str, str]:
    """Health check endpoint for API clients (OBS-02)."""
    return {"status": "healthy", "service": "api"}


@router.get("/ready")
async def api_ready() -> dict[str, Any]:
    """Readiness probe endpoint for API clients (OBS-02)."""
    from core.health import check_readiness
    return check_readiness()


@router.get("/metrics")
async def api_metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint for API clients (OBS-03)."""
    from core.metrics import get_metrics_registry
    return PlainTextResponse(
        get_metrics_registry().generate_metrics_text(),
        media_type="text/plain; version=0.0.4",
    )
