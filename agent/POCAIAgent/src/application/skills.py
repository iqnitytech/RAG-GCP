from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import replace
from typing import Any, Callable, Mapping

from application.manifest import CatalogEntry, SkillRecord
from application.registry import AgentRegistry
from domain.errors import AuthorizationError, DomainError, NotFoundError, UpstreamError
from domain.ports import RegistryStore, SkillInfo, UserContext

log = logging.getLogger("skills")

Skill = SkillInfo


def _hash(description: str, schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps([description, schema], sort_keys=True, default=str).encode()).hexdigest()


class SkillRegistry:
    """Governed skill catalog.

    Tools are discovered from each agent's MCP server and reconciled with the RegistryStore:
    new or changed tools are stored as 'pending' (or 'approved' when auto_approve, local only).
    Only 'approved' skills are listed/invokable, and a record's required_roles override the agent's roles.
    Discovery + reconciliation are cached per agent for ttl_s, so the store is not hit on every request.
    """

    def __init__(self, agents: AgentRegistry, store: RegistryStore, auto_approve: bool = False,
                 ttl_s: float = 300, clock: Callable[[], float] = time.monotonic,
                 catalog: Mapping[str, CatalogEntry] | None = None) -> None:
        self._agents, self._store, self._auto = agents, store, auto_approve
        self._ttl, self._clock = ttl_s, clock
        self._catalog = dict(catalog or {})
        self._cache: dict[str, tuple[float, dict[str, SkillRecord]]] = {}

    async def _records(self, agent, ctx: UserContext) -> dict[str, SkillRecord]:
        hit = self._cache.get(agent.name)
        if hit and self._clock() - hit[0] < self._ttl:
            return hit[1]
        records: dict[str, SkillRecord] = {}
        for tool in await agent.mcp.list_tools(ctx):
            records[tool.name] = await self._reconcile(agent.name, tool)
        for entry in self._catalog.values():
            if entry.agent == agent.name and entry.id.split(".", 1)[1] not in records:
                log.warning("catalog skill %s has no matching MCP tool", entry.id)
        self._cache[agent.name] = (self._clock(), records)
        return records

    @staticmethod
    def _overlay(record: SkillRecord, entry: CatalogEntry | None) -> SkillRecord:
        """Apply reviewed catalog metadata. The catalog can disable a skill and sets roles/tags/examples."""
        if entry is None:
            return record
        return replace(
            record, name=entry.name, tags=entry.tags, examples=entry.examples, owner=entry.owner,
            required_roles=entry.required_roles or record.required_roles,
            status="disabled" if entry.status == "disabled" else record.status,
        )

    async def _reconcile(self, agent_name: str, tool) -> SkillRecord:
        skill_id = f"{agent_name}.{tool.name}"
        entry = self._catalog.get(skill_id)
        digest = _hash(tool.description, tool.input_schema)
        current = await self._store.get_skill(skill_id)
        if current is None:
            # brand-new tool: live only if auto-approve (local) or the reviewed catalog approves it
            status = "approved" if self._auto or (entry and entry.status == "approved") else "pending"
            record = SkillRecord(skill_id, agent_name, tool.name, tool.description, dict(tool.input_schema),
                                 digest, status=status)
        elif current.schema_hash != digest:
            # changed contract: needs re-approval even if the catalog says approved (disabled stays disabled)
            record = replace(current, description=tool.description, input_schema=dict(tool.input_schema),
                             schema_hash=digest, version=current.version + 1,
                             status="disabled" if current.status == "disabled"
                             else ("approved" if self._auto else "pending"))
        else:
            return self._overlay(current, entry)
        record = self._overlay(record, entry)
        await self._store.upsert_skill(record)
        return record

    def invalidate(self, agent_name: str | None = None) -> None:
        self._cache.pop(agent_name, None) if agent_name else self._cache.clear()

    @staticmethod
    def _permitted(record: SkillRecord, agent, ctx: UserContext) -> bool:
        required = record.required_roles or tuple(agent.required_roles)
        return record.status == "approved" and (not required or ctx.has_any(*required))

    async def list_for(self, ctx: UserContext) -> list[Skill]:
        skills: list[Skill] = []
        for agent in self._agents.allowed_for(ctx):
            if not hasattr(agent, "mcp"):
                continue
            try:
                records = await self._records(agent, ctx)
            except Exception as exc:
                log.warning("skill discovery failed for %s: %r", agent.name, exc)
                continue
            skills += [Skill(r.id, r.agent, r.tool, r.description, r.input_schema, r.name, r.tags, r.examples)
                       for r in records.values() if self._permitted(r, agent, ctx)]
        return skills

    async def invoke(self, skill_id: str, args: Mapping[str, Any], ctx: UserContext) -> Any:
        agent_name, _, tool_name = skill_id.partition(".")
        agent = self._agents.get(agent_name)
        if agent is None or not hasattr(agent, "mcp"):
            raise NotFoundError(f"Unknown skill: {skill_id}")
        if agent not in self._agents.allowed_for(ctx):
            raise AuthorizationError(f"Not allowed to use {agent_name}")
        try:
            records = await self._records(agent, ctx)
        except Exception as exc:
            raise UpstreamError(f"Skill discovery failed for {agent_name}") from exc
        record = records.get(tool_name)
        if record is None or record.status != "approved":
            raise NotFoundError(f"Unknown skill: {skill_id}")
        if not self._permitted(record, agent, ctx):
            raise AuthorizationError(f"Not allowed to use {skill_id}")
        props = record.input_schema.get("properties", {})
        missing = [p for p in record.input_schema.get("required", []) if p not in args]
        unknown = [a for a in args if a not in props]
        if missing or unknown:
            raise DomainError(f"Invalid arguments (missing={missing}, unknown={unknown})")
        return await agent.mcp.call_tool(tool_name, args, ctx)
