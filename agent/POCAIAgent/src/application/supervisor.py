from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from application.planning import fanout_ids, find_ids, plan_agents
from application.registry import AgentRegistry
from domain.errors import AuthorizationError
from domain.ports import Agent, LLMProvider, RoutingStrategy, UserContext

log = logging.getLogger("supervisor")


class SkillSelectorPort(Protocol):
    async def select(self, message: str, ctx: UserContext) -> list[str]: ...


@dataclass
class ChatResult:
    answer: str
    agents_used: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    skills_selected: list[str] = field(default_factory=list)   # ids chosen by the skill selector (if any)


class SupervisorAgent:
    """Hierarchy top: (1) the LLM picks skills from the caller's permitted catalog, (2) if it is unsure or
    unavailable, the routing Strategy picks agents (keyword -> skill -> LLM), (3) dependent steps are planned and
    ordered by the consumes/produces rules, (4) agents run, results are aggregated."""

    def __init__(self, registry: AgentRegistry, strategy: RoutingStrategy, llm: LLMProvider,
                 selector: SkillSelectorPort | None = None) -> None:
        self._registry, self._strategy, self._llm, self._selector = registry, strategy, llm, selector

    async def _choose(self, message: str, ctx: UserContext, allowed: Sequence[Agent]):
        """Returns (agents, tools_by_agent, selected_skill_ids)."""
        if self._selector is not None:
            try:
                skill_ids = await self._selector.select(message, ctx)
            except Exception as exc:  # LLM/registry trouble must not take chat down
                log.warning("skill selection failed, falling back to routing: %r", exc)
                skill_ids = []
            by_name = {a.name: a for a in allowed}
            tools: dict[str, list[str]] = {}
            for sid in skill_ids:
                agent_name, _, tool = sid.partition(".")
                if agent_name in by_name:
                    tools.setdefault(agent_name, []).append(tool)
            if tools:
                return [by_name[n] for n in tools], tools, skill_ids
        return await self._strategy.select(message, allowed, ctx), {}, []

    async def handle(self, message: str, ctx: UserContext) -> ChatResult:
        allowed = self._registry.allowed_for(ctx)
        if not allowed:
            return ChatResult("You are not authorised to use any capability.")
        selected, tools, skill_ids = await self._choose(message, ctx, allowed)
        if not selected:
            forbidden = await self._strategy.select(message, self._registry.all(), ctx)
            if forbidden:
                return ChatResult("You are not authorised for the requested information.")
            return ChatResult("I could not tell which capability (machine, customer, telemetry) you need.")

        parts: dict[str, str] = {}
        errors: dict[str, str] = {}
        discovered: dict[str, list[str]] = {}
        for stage in plan_agents(selected, allowed, message):
            outcomes = await asyncio.gather(
                *(self._run(a, message, ctx, discovered, tools.get(a.name)) for a in stage),
                return_exceptions=True,
            )
            for agent, outcome in zip(stage, outcomes):
                if isinstance(outcome, AuthorizationError):
                    errors[agent.name] = "forbidden"
                elif isinstance(outcome, Exception):
                    log.warning("agent %s failed: %r", agent.name, outcome)
                    errors[agent.name] = "unavailable"
                else:
                    parts[agent.name] = outcome
                    for kind in getattr(agent, "produces", ()):
                        found = find_ids(kind, outcome)
                        discovered[kind] = list(dict.fromkeys(discovered.get(kind, []) + found))
        if not parts:
            return ChatResult("Sorry, I could not retrieve that information right now.", errors=errors,
                              skills_selected=skill_ids)
        answer = await self._llm.synthesize(message, parts)
        return ChatResult(answer, agents_used=list(parts), errors=errors, skills_selected=skill_ids)

    async def _run(self, agent: Agent, message: str, ctx: UserContext, discovered: Mapping[str, list[str]],
                   only_tools: Sequence[str] | None) -> str:
        ids = fanout_ids(agent, message, discovered)
        if not ids:
            return await agent.handle(message, ctx, only_tools)
        outcomes = await asyncio.gather(
            *(agent.handle(f"{message} {i}", ctx, only_tools) for i in ids), return_exceptions=True
        )
        ok = [o for o in outcomes if isinstance(o, str)]
        if not ok:
            raise next(o for o in outcomes if isinstance(o, Exception))
        return "\n".join(ok)
