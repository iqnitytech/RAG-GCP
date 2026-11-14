import json

import pytest

from container import Providers, build_container
from domain.ports import ToolSpec
from infrastructure.di import Singleton
from infrastructure.llm.vertex import VertexGeminiLLM, VertexModels
from settings import Settings
from conftest import ROOT

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_providers_are_singletons_and_shared():
    p = Providers(Settings(agents_config=str(ROOT / "config" / "agents.yaml")))
    assert p.llm() is p.llm() and p.registry() is p.registry() and p.supervisor() is p.supervisor()
    c1, c2 = build_container(p.settings(), p), build_container(p.settings(), p)
    assert c1.registry is c2.registry and c1.chat is c2.chat
    assert all(a.llm is p.llm() for a in c1.registry.all())        # one LLM shared by every agent


def test_singleton_creates_once_and_can_be_overridden():
    calls = []
    s = Singleton(lambda: calls.append(1) or object())
    assert s() is s() and len(calls) == 1
    s.reset()
    s()
    assert len(calls) == 2


class _Res:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def __init__(self, replies):
        self.replies, self.models_used = list(replies), []
        outer = self

        class _Models:
            async def generate_content(self, model, contents, config):
                outer.models_used.append(model)
                return _Res(outer.replies.pop(0))

        class _Aio:
            models = _Models()

        self.aio = _Aio()


async def test_vertex_uses_a_model_per_role_and_survives_bad_json():
    client = _FakeClient([json.dumps({"name": "get_machine", "args": {"machine_id": "M-1"}}),
                          "not json", "final answer"])
    llm = VertexGeminiLLM(client, VertexModels("m-route", "m-tools", "m-synth"))
    tools = [ToolSpec("get_machine", "d", {"properties": {"machine_id": {}}})]
    call = await llm.pick_tool("status of M-1", tools)
    assert call.name == "get_machine" and call.args == {"machine_id": "M-1"}
    assert await llm.choose_agents("x", []) == []                  # malformed JSON -> graceful
    assert await llm.synthesize("q", {"machine": "{}"}) == "final answer"
    assert client.models_used == ["m-tools", "m-route", "m-synth"]


def test_settings_models_and_credentials(monkeypatch):
    monkeypatch.setenv("VERTEX_MODEL_SYNTHESIS", "gemini-x")
    s = Settings.from_env()
    assert s.vertex_model_synthesis == "gemini-x" and s.vertex_model_routing == "gemini-2.5-flash"
    with pytest.raises(RuntimeError):
        Settings(llm_mode="vertex").validate()
    Settings(llm_mode="vertex", vertex_project="p").validate()
    Settings(llm_mode="vertex", vertex_api_key="k").validate()
