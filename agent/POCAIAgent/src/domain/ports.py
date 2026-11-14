"""Ports (interfaces). The domain/application layers depend only on these."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .entities import Customer, Machine, TelemetryReading

PLATFORM_ADMIN = "Platform.Admin"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    tenant_id: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_any(self, *roles: str) -> bool:
        return PLATFORM_ADMIN in self.roles or bool(self.roles.intersection(roles))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True)
class SkillInfo:
    """An approved, callable capability. id = '<agent>.<tool>'."""

    id: str
    agent: str
    tool: str
    description: str
    input_schema: Mapping[str, Any]
    name: str = ""
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Text used to match/embed this skill."""
        return " ".join([self.name, self.tool.replace("_", " "), self.description, *self.tags, *self.examples])


# ---------- Identity ----------
class TokenValidator(Protocol):
    async def validate(self, token: str) -> UserContext: ...


# ---------- Salesforce (mock or SDK adapter) ----------
class SalesforcePort(Protocol):
    async def query(
        self,
        sobject: str,
        fields: Sequence[str],
        where: Mapping[str, str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def search(
        self, sobject: str, fields: Sequence[str], field: str, text: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...


# ---------- Repositories (narrow interfaces, ISP) ----------
class MachineRepository(Protocol):
    async def get(self, machine_id: str) -> Machine | None: ...
    async def list_by_customer(self, customer_id: str) -> list[Machine]: ...


class CustomerRepository(Protocol):
    async def get(self, customer_id: str) -> Customer | None: ...
    async def search(self, name: str) -> list[Customer]: ...


class TelemetryRepository(Protocol):
    async def latest(self, machine_id: str, limit: int = 10) -> list[TelemetryReading]: ...


# ---------- MCP ----------
class McpToolClient(Protocol):
    async def list_tools(self, ctx: UserContext) -> list[ToolSpec]: ...
    async def call_tool(self, name: str, args: Mapping[str, Any], ctx: UserContext) -> Any: ...


# ---------- LLM ----------
class LLMProvider(Protocol):
    async def pick_tool(self, message: str, tools: Sequence[ToolSpec]) -> ToolCall | None: ...
    async def choose_agents(self, message: str, agents: Sequence["Agent"]) -> list[str]: ...
    async def synthesize(self, message: str, parts: Mapping[str, str]) -> str: ...
    async def select_skills(self, message: str, skills: Sequence[SkillInfo], max_steps: int) -> list[str]: ...


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


# ---------- Registry metadata store ----------
class RegistryStore(Protocol):
    def list_agents(self) -> list[Any]: ...                      # list[AgentManifest], read at startup
    async def get_skill(self, skill_id: str) -> Any | None: ...  # SkillRecord | None
    async def upsert_skill(self, record: Any) -> None: ...


# ---------- Agents ----------
@runtime_checkable
class Agent(Protocol):
    name: str
    description: str
    required_roles: frozenset[str]

    async def handle(self, message: str, ctx: UserContext, only_tools: Sequence[str] | None = None) -> str: ...


class RoutingStrategy(Protocol):
    async def select(self, message: str, agents: Sequence[Agent], ctx: UserContext) -> list[Agent]: ...
