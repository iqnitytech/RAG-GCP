import pytest

from application.manifest import SkillRecord
from application.skills import SkillRegistry
from container import build_container
from domain.ports import UserContext
from infrastructure.registry.firestore_store import FirestoreRegistryStore
from infrastructure.registry.yaml_store import YamlRegistryStore
from settings import Settings
from conftest import ROOT

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make(auto: bool, store=None):
    c = build_container(Settings(agents_config=str(ROOT / "config" / "agents.yaml")))
    store = store or YamlRegistryStore(str(ROOT / "config" / "agents.yaml"))
    return SkillRegistry(c.registry, store, auto_approve=auto), store


ADMIN = UserContext("u", "t", frozenset({"Platform.Admin"}))


async def test_new_skills_are_pending_until_approved():
    skills, store = make(auto=False)
    assert await skills.list_for(ADMIN) == []
    pending = await store.get_skill("machine.get_machine")
    assert pending.status == "pending"
    await store.upsert_skill(SkillRecord(**{**pending.__dict__, "status": "approved"}))
    skills.invalidate()
    assert [s.id for s in await skills.list_for(ADMIN)] == ["machine.get_machine"]


async def test_record_roles_override_agent_roles_and_changed_schema_requires_reapproval():
    skills, store = make(auto=False)
    await skills.list_for(ADMIN)
    rec = await store.get_skill("machine.get_machine")
    await store.upsert_skill(SkillRecord(**{**rec.__dict__, "status": "approved", "required_roles": ("Special.Role",)}))
    skills.invalidate()
    user = UserContext("u", "t", frozenset({"Machine.Read"}))
    assert await skills.list_for(user) == []                       # agent role no longer enough
    assert len(await skills.list_for(ADMIN)) == 1                  # admin always passes
    await store.upsert_skill(SkillRecord(**{**rec.__dict__, "status": "approved", "schema_hash": "stale"}))
    skills.invalidate()
    assert await skills.list_for(ADMIN) == []                      # contract changed -> pending again
    assert (await store.get_skill("machine.get_machine")).version == 2


class _Doc:
    def __init__(self, id, data):
        self.id, self._d = id, data
        self.exists = data is not None

    def to_dict(self):
        return self._d


class _Ref:
    def __init__(self, col, id):
        self.col, self.id = col, id

    def get(self):
        return _Doc(self.id, self.col.docs.get(self.id))

    def set(self, d):
        self.col.docs[self.id] = dict(d)

    def update(self, d):
        self.col.docs[self.id].update(d)


class _Col:
    def __init__(self):
        self.docs = {}

    def document(self, id):
        return _Ref(self, id)

    def stream(self):
        return [_Doc(i, d) for i, d in self.docs.items()]

    def where(self, f, op, v):
        out = _Col()
        out.docs = {i: d for i, d in self.docs.items() if d.get(f) == v}
        return out


class _Client:
    def __init__(self):
        self.cols = {}

    def collection(self, name):
        return self.cols.setdefault(name, _Col())


async def test_firestore_store_roundtrip_and_agent_loading():
    from infrastructure.manifest_loader import load_manifests

    store = FirestoreRegistryStore(_Client())
    for m in load_manifests(str(ROOT / "config" / "agents.yaml")):
        store.put_agent(m)
    agents = {a.name: a for a in store.list_agents()}
    assert agents["telemetry"].consumes == ("machine_id",) and agents["machine"].order == 1

    skills, _ = make(auto=False, store=store)
    await skills.list_for(ADMIN)
    assert len(store.list_by_status("pending")) == 6
    store.set_status("machine.get_machine", "approved")
    skills.invalidate()
    assert [s.id for s in await skills.list_for(ADMIN)] == ["machine.get_machine"]
    store.put_agent(agents["machine"], enabled=False)
    assert "machine" not in {a.name for a in store.list_agents()}


def test_prod_requires_firestore_and_manual_approval():
    base = dict(env="prod", auth_mode="entra", entra_tenant_id="t", entra_audience="a",
                user_context_secret="x", mcp_auth="google")
    with pytest.raises(RuntimeError):
        Settings(**base, registry_store="yaml", skills_auto_approve=False).validate()
    with pytest.raises(RuntimeError):
        Settings(**base, registry_store="firestore", skills_auto_approve=True).validate()
    Settings(**base, registry_store="firestore", skills_auto_approve=False).validate()
