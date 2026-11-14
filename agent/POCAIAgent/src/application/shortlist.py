from __future__ import annotations

import hashlib
import logging
import math
from typing import Sequence

from domain.ports import Embedder, SkillInfo

log = logging.getLogger("shortlist")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class SkillShortlister:
    """Keeps the LLM prompt small as the catalog grows: when there are more than `size` skills, only the
    `size` most similar to the question (embedding cosine) are shown to the LLM. Skill vectors are cached."""

    def __init__(self, embedder: Embedder, size: int = 12) -> None:
        self._embedder, self._size = embedder, size
        self._vectors: dict[tuple[str, str], list[float]] = {}

    @staticmethod
    def _key(skill: SkillInfo) -> tuple[str, str]:
        return skill.id, hashlib.sha1(skill.text.encode()).hexdigest()

    async def top(self, message: str, skills: Sequence[SkillInfo]) -> list[SkillInfo]:
        if len(skills) <= self._size:
            return list(skills)
        try:
            missing = [s for s in skills if self._key(s) not in self._vectors]
            if missing:
                for s, vec in zip(missing, await self._embedder.embed([s.text for s in missing])):
                    self._vectors[self._key(s)] = vec
            (query,) = await self._embedder.embed([message])
        except Exception as exc:
            log.warning("shortlist failed, using the full catalog: %r", exc)
            return list(skills)
        ranked = sorted(skills, key=lambda s: _cosine(query, self._vectors[self._key(s)]), reverse=True)
        return ranked[: self._size]
