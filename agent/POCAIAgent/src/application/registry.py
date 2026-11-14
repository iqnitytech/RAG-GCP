from __future__ import annotations

from domain.ports import Agent, UserContext


class AgentRegistry:
    """Open/Closed: new agents are registered, the supervisor never changes."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if agent.name in self._agents:
            raise ValueError(f"Agent already registered: {agent.name}")
        self._agents[agent.name] = agent

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def allowed_for(self, ctx: UserContext) -> list[Agent]:
        return [a for a in self._agents.values() if not a.required_roles or ctx.has_any(*a.required_roles)]
