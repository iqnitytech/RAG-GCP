from __future__ import annotations

import re
from typing import Sequence

from application.skills import SkillRegistry
from domain.ports import Agent, LLMProvider, UserContext

_STOP = {"the", "and", "for", "with", "all", "show", "get", "list", "give", "tell", "about", "what", "which",
         "how", "are", "is", "of", "me", "my", "its", "their", "please", "info", "information", "details", "data"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", text.lower()) if len(t) >= 3 and t not in _STOP}


def _matches(token: str, word: str) -> bool:
    """Prefix match either way, so 'tele' -> 'telemetry' and 'machines' -> 'machine'."""
    return word.startswith(token) or (len(word) >= 4 and token.startswith(word))


class KeywordRouting:
    """Fast, free, deterministic. Keywords come from each agent's manifest."""

    async def select(self, message: str, agents: Sequence[Agent], ctx: UserContext) -> list[Agent]:
        words = set(re.findall(r"[a-z0-9]+", message.lower()))
        return [a for a in agents if any(k.lower() in words for k in getattr(a, "keywords", ()))]


class SkillRouting:
    """Routes by what agents can actually do: matches the question against each agent's approved skills
    (tool names + descriptions, from the SkillRegistry) plus its own name/description/keywords.
    Tolerates partial words ('tele'). No LLM call; new skills are routable as soon as they are approved.
    """

    def __init__(self, skills: SkillRegistry, min_relative_score: float = 0.5) -> None:
        self._skills, self._min = skills, min_relative_score

    async def select(self, message: str, agents: Sequence[Agent], ctx: UserContext) -> list[Agent]:
        query = _tokens(message)
        if not query:
            return []
        wanted = {a.name for a in agents}
        docs: dict[str, set[str]] = {
            a.name: _tokens(" ".join([a.name, a.description, *getattr(a, "keywords", ())])) for a in agents
        }
        for skill in await self._skills.list_for(ctx):        # only approved skills the caller may use
            if skill.agent in wanted:
                docs[skill.agent] |= _tokens(
                    f"{skill.tool.replace('_', ' ')} {skill.description} {skill.name} "
                    f"{' '.join(skill.tags)} {' '.join(skill.examples)}"
                )
        scores = {name: sum(any(_matches(q, w) for w in words) for q in query) for name, words in docs.items()}
        best = max(scores.values(), default=0)
        if best == 0:
            return []
        return [a for a in agents if scores[a.name] > 0 and scores[a.name] >= best * self._min]


class LlmRouting:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def select(self, message: str, agents: Sequence[Agent], ctx: UserContext) -> list[Agent]:
        names = set(await self._llm.choose_agents(message, agents))
        return [a for a in agents if a.name in names]


class HybridRouting:
    """Tries each strategy in order (e.g. keyword -> skill -> LLM); the first non-empty answer wins."""

    def __init__(self, *strategies) -> None:
        self._strategies = strategies

    async def select(self, message: str, agents: Sequence[Agent], ctx: UserContext) -> list[Agent]:
        for strategy in self._strategies:
            selected = await strategy.select(message, agents, ctx)
            if selected:
                return selected
        return []
