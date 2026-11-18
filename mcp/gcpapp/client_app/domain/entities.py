from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class ToolParameter:
    name: str
    type_name: str
    required: bool
    description: str
    default: Optional[Any] = None
    example: Optional[Any] = None


@dataclass(frozen=True)
class ToolDescriptor:
    server_id: str
    name: str
    description: str
    parameters: list[ToolParameter]
    preset_arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServerDescriptor:
    server_id: str
    display_name: str
    description: str
    tools: list[ToolDescriptor]


@dataclass(frozen=True)
class ToolInvocationRequest:
    server_id: str
    tool_name: str
    arguments: dict[str, Any]
    mode: str = "MOCK"


@dataclass(frozen=True)
class ToolInvocationResult:
    server_id: str
    tool_name: str
    status: str  # "success" | "error"
    payload: dict[str, Any]
    duration_ms: float
    timestamp: str
    raw_error: Optional[str] = None


@dataclass(frozen=True)
class ScenarioStep:
    step_id: int
    name: str
    server_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    title: str
    total_steps: int
    passed_steps: int
    duration_ms: float
    step_results: list[ToolInvocationResult]
