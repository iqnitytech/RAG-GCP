from __future__ import annotations

import json
from dataclasses import asdict

import anyio

from application.manifest import AgentManifest, SkillRecord


class FirestoreRegistryStore:
    """Prod store. Collections: <prefix>_agents (doc id = agent name) and <prefix>_skills (doc id = skill id).

    `client` is a google.cloud.firestore.Client (ADC; the service account needs roles/datastore.user,
    read-only roles/datastore.viewer is enough for chat-api if skill sync is done by the admin job).
    """

    def __init__(self, client, prefix: str = "registry") -> None:
        self._agents = client.collection(f"{prefix}_agents")
        self._skills = client.collection(f"{prefix}_skills")

    # ----- agents (read at startup; managed by registry admins) -----
    def list_agents(self) -> list[AgentManifest]:
        manifests = []
        for doc in self._agents.stream():
            d = doc.to_dict()
            if not d.get("enabled", True):
                continue
            manifests.append(
                AgentManifest(
                    name=doc.id,
                    description=d["description"],
                    transport=d["transport"],
                    endpoint=d.get("endpoint", ""),
                    required_roles=tuple(d.get("required_roles", [])),
                    keywords=tuple(d.get("keywords", [])),
                    order=int(d.get("order", 0)),
                    consumes=tuple(d.get("consumes", [])),
                    produces=tuple(d.get("produces", [])),
                    version=str(d.get("version", "1.0.0")),
                    owner=d.get("owner", ""),
                    tags=tuple(d.get("tags", [])),
                )
            )
        return manifests

    def put_agent(self, m: AgentManifest, enabled: bool = True) -> None:
        data = {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(m).items() if k != "name"}
        self._agents.document(m.name).set({**data, "enabled": enabled})

    # ----- skills -----
    async def get_skill(self, skill_id: str) -> SkillRecord | None:
        doc = await anyio.to_thread.run_sync(self._skills.document(skill_id).get)
        return self._to_record(doc.id, doc.to_dict()) if doc.exists else None

    async def upsert_skill(self, record: SkillRecord) -> None:
        await anyio.to_thread.run_sync(self._skills.document(record.id).set, self._to_doc(record))

    def set_status(self, skill_id: str, status: str) -> None:
        self._skills.document(skill_id).update({"status": status})

    def list_by_status(self, status: str) -> list[SkillRecord]:
        return [self._to_record(d.id, d.to_dict()) for d in self._skills.where("status", "==", status).stream()]

    @staticmethod
    def _to_doc(r: SkillRecord) -> dict:
        d = asdict(r)
        d.pop("id")
        d["input_schema"] = json.dumps(r.input_schema, sort_keys=True)  # Firestore-safe (no key restrictions)
        d["required_roles"] = list(r.required_roles)
        d["tags"], d["examples"] = list(r.tags), list(r.examples)
        return d

    @staticmethod
    def _to_record(skill_id: str, d: dict) -> SkillRecord:
        return SkillRecord(
            id=skill_id, agent=d["agent"], tool=d["tool"], description=d["description"],
            input_schema=json.loads(d["input_schema"]), schema_hash=d["schema_hash"],
            required_roles=tuple(d.get("required_roles", [])), version=int(d.get("version", 1)),
            status=d.get("status", "pending"), name=d.get("name", ""), tags=tuple(d.get("tags", [])),
            examples=tuple(d.get("examples", [])), owner=d.get("owner", ""),
        )
