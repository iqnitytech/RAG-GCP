import json

import pytest

from application.shortlist import SkillShortlister
from application.skill_selector import LlmSkillSelector
from application.supervisor import SupervisorAgent
from conftest import ROOT
from container import Providers, build_container
from domain.ports import SkillInfo, UserContext
from infrastructure.di import Singleton
from infrastructure.embeddings.hashing import HashingEmbedder
from infrastructure.llm.vertex import VertexGeminiLLM, VertexModels
from routing_eval import run_eval
from settings import Settings

pytestmark = pytest.mark.anyio
ADMIN = UserContext("u", "t", frozenset({"Platform.Admin"}))
AGENTS = str(ROOT / "config" / "agents.yaml")


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_routing_accuracy_gate_with_mock_llm():
    report = await run_eval(Settings(agents_config=AGENTS), ROOT / "tests" / "eval" / "routing_cases.json")
    assert report.accuracy >= 90, "\n".join(f"{r.question}: {r.reason}" for r in report.failures)
    assert report.skipped > 0  # the 'llm' tier is only measured with Vertex


async def test_selector_drops_unknown_duplicate_and_over_cap_ids():
    p = Providers(Settings(agents_config=AGENTS))

    class FakeLLM:
        async def select_skills(self, message, skills, max_steps):
            return ["ghost.tool", "machine.get_machine", "machine.get_machine", "customer.get_customer",
                    "telemetry.anomalies", "telemetry.latest_readings"]

    sel = LlmSkillSelector(p.skills(), FakeLLM(), None, max_steps=2)
    assert await sel.select("anything", ADMIN) == ["machine.get_machine", "customer.get_customer"]


async def test_selector_only_offers_permitted_skills():
    p = Providers(Settings(agents_config=AGENTS))
    offered: list[str] = []

    class SpyLLM:
        async def select_skills(self, message, skills, max_steps):
            offered.extend(s.id for s in skills)
            return ["telemetry.anomalies"]            # LLM tries to pick a skill the user may not use

    sel = LlmSkillSelector(p.skills(), SpyLLM(), None)
    user = UserContext("u", "t", frozenset({"Machine.Read"}))
    assert await sel.select("x", user) == []
    assert offered and all(i.startswith("machine.") for i in offered)


async def test_supervisor_falls_back_to_routing_when_selector_fails():
    p = Providers(Settings(agents_config=AGENTS))

    class Broken:
        async def select(self, message, ctx):
            raise RuntimeError("llm down")

    sup = SupervisorAgent(p.registry(), p.routing(), p.llm(), Broken())
    res = await sup.handle("status of machine M-1", ADMIN)
    assert res.agents_used == ["machine"] and res.skills_selected == []


async def test_selected_skill_pins_the_tool():
    p = Providers(Settings(agents_config=AGENTS))

    class Pick:
        async def select(self, message, ctx):
            return ["machine.list_machines_by_customer"]

    sup = SupervisorAgent(p.registry(), p.routing(), p.llm(), Pick())
    res = await sup.handle("machine M-1 and customer C-1", ADMIN)   # ids for both tools present
    assert "M-2" in res.answer and res.skills_selected == ["machine.list_machines_by_customer"]


def _skills(n: int) -> list[SkillInfo]:
    topics = ["invoice billing payment", "warranty contract coverage", "shipping logistics delivery",
              "inventory stock warehouse", "firmware update version", "maintenance schedule technician"]
    return [SkillInfo(f"a{i}.t{i}", f"a{i}", f"t{i}", topics[i % len(topics)] + f" {i}", {}, tags=(topics[i % 6],))
            for i in range(n)]


async def test_shortlister_keeps_relevant_skills_and_skips_small_catalogs():
    sl = SkillShortlister(HashingEmbedder(), size=5)
    small = _skills(4)
    assert await sl.top("anything", small) == small
    big = _skills(30)
    top = await sl.top("when does the technician maintenance visit happen", big)
    assert len(top) == 5 and all("maintenance" in s.description for s in top)


async def test_shortlister_degrades_to_full_catalog_when_embedder_fails():
    class Boom:
        async def embed(self, texts):
            raise RuntimeError("down")

    big = _skills(30)
    assert await SkillShortlister(Boom(), size=5).top("x", big) == big


class _Res:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def __init__(self, reply):
        self.reply, self.model = reply, None
        outer = self

        class _Models:
            async def generate_content(self, model, contents, config):
                outer.model = model
                return _Res(outer.reply)

        class _Aio:
            models = _Models()

        self.aio = _Aio()


async def test_vertex_select_skills_validates_output():
    skills = _skills(3)
    client = _FakeClient(json.dumps(["a0.t0", "nope", "a2.t2", 7]))
    llm = VertexGeminiLLM(client, VertexModels("m-route", "m-tools", "m-synth"))
    assert await llm.select_skills("q", skills, 1) == ["a0.t0"]
    assert client.model == "m-route"
    assert await VertexGeminiLLM(_FakeClient("garbage"), VertexModels("a", "b", "c")).select_skills("q", skills, 3) == []


def test_selection_can_be_switched_off_and_settings_validated():
    p = Providers(Settings(agents_config=AGENTS, skill_selection="off"))
    assert p.selector() is None
    for bad in (dict(skill_selection="x"), dict(embedder="x"), dict(skill_max_steps=0),
                dict(embedder="vertex")):
        with pytest.raises(RuntimeError):
            Settings(**bad).validate()
    p2 = Providers(Settings(agents_config=AGENTS))
    p2.embedder = Singleton(lambda: None)
    assert p2.selector() is not None
