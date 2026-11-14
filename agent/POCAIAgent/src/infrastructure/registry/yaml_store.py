from __future__ import annotations

from application.manifest import AgentManifest, SkillRecord
from infrastructure.manifest_loader import load_manifests


class YamlRegistryStore:
    """Local/dev store: agents from YAML, skill records in memory (lost on restart)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._skills: dict[str, SkillRecord] = {}

    def list_agents(self) -> list[AgentManifest]:
        return load_manifests(self._path)

    async def get_skill(self, skill_id: str) -> SkillRecord | None:
        return self._skills.get(skill_id)

    async def upsert_skill(self, record: SkillRecord) -> None:
        self._skills[record.id] = record
