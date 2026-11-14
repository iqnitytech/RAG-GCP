from __future__ import annotations

import logging

from application.shortlist import SkillShortlister
from application.skills import SkillRegistry
from domain.ports import LLMProvider, UserContext

log = logging.getLogger("skill_selector")


class LlmSkillSelector:
    """Asks the LLM which skills answer the question (one step, skills directly).

    The LLM only sees skills the caller is allowed to use (approved + role-permitted); its answer is validated
    against that list, de-duplicated and capped at max_steps, so it can never name an unknown or forbidden skill.
    """

    def __init__(self, skills: SkillRegistry, llm: LLMProvider, shortlister: SkillShortlister | None = None,
                 max_steps: int = 4) -> None:
        self._skills, self._llm, self._shortlister, self._max = skills, llm, shortlister, max_steps

    async def select(self, message: str, ctx: UserContext) -> list[str]:
        offered = await self._skills.list_for(ctx)
        if not offered:
            return []
        if self._shortlister:
            offered = await self._shortlister.top(message, offered)
        valid = {s.id for s in offered}
        chosen = await self._llm.select_skills(message, offered, self._max)
        result = [sid for sid in dict.fromkeys(chosen) if sid in valid][: self._max]
        if len(result) != len(chosen):
            log.info("dropped %d invalid/duplicate/over-cap skill ids from LLM output", len(chosen) - len(result))
        return result
