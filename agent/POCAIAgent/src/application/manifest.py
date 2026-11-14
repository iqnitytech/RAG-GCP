from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentManifest:
    """Declarative description of an agent/MCP. Adding a capability = adding a manifest."""

    name: str
    description: str
    transport: str                       # inprocess | streamable_http
    endpoint: str = ""                   # URL (streamable_http) or catalog key (inprocess)
    required_roles: tuple[str, ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)
    order: int = 0
    consumes: tuple[str, ...] = field(default_factory=tuple)
    produces: tuple[str, ...] = field(default_factory=tuple)
    version: str = "1.0.0"
    owner: str = ""                      # owning team / contact
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SkillRecord:
    """Governed metadata for a skill. Only status == 'approved' skills are exposed or invokable."""

    id: str
    agent: str
    tool: str
    description: str
    input_schema: dict
    schema_hash: str
    required_roles: tuple[str, ...] = field(default_factory=tuple)   # empty = inherit the agent's roles
    version: int = 1
    status: str = "pending"                                           # pending | approved | disabled
    name: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)
    owner: str = ""


@dataclass(frozen=True)
class CatalogEntry:
    """Reviewed (git/PR) metadata for a skill, from config/skills/*.json. The MCP tool stays the source of
    truth for the executable contract; this adds name, tags, example questions, roles, owner and approval."""

    id: str
    agent: str
    name: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    examples: tuple[str, ...] = field(default_factory=tuple)
    required_roles: tuple[str, ...] = field(default_factory=tuple)
    version: str = "1.0.0"
    status: str = "pending"                                           # pending | approved | disabled
    owner: str = ""
