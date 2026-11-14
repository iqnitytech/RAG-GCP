"""Composition root: the only place that knows concrete classes.

Every shared service is a lazy, thread-safe Singleton provider (one LLM client, one Salesforce session,
one registry store, one set of MCP clients ...). Providers reference each other, so construction order
is resolved automatically and tests can swap any provider before first use.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from application.chat_service import HandleChatUseCase
from application.mcp_agent import McpAgent
from application.registry import AgentRegistry
from application.routing.strategies import HybridRouting, KeywordRouting, LlmRouting, SkillRouting
from application.shortlist import SkillShortlister
from application.skill_selector import LlmSkillSelector
from application.skills import SkillRegistry
from application.supervisor import SupervisorAgent
from domain.ports import Embedder, LLMProvider, RegistryStore, RoutingStrategy, SalesforcePort, TokenValidator
from infrastructure.auth.validators import DevTokenValidator, EntraTokenValidator
from infrastructure.di import Singleton
from infrastructure.embeddings.factory import build_embedder
from infrastructure.llm.factory import build_llm
from infrastructure.mcp.factory import build_mcp_client
from infrastructure.registry.factory import build_registry_store
from infrastructure.registry.skill_catalog import load_skill_catalog
from infrastructure.salesforce.factory import build_salesforce
from settings import Settings


def build_validator(settings: Settings) -> TokenValidator:
    if settings.auth_mode == "entra":
        return EntraTokenValidator(settings.entra_tenant_id, settings.entra_audience)
    return DevTokenValidator()


class Providers:
    """Singleton providers. Override a provider (e.g. `p.llm = Singleton(lambda: fake)`) before first use in tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings: Singleton[Settings] = Singleton(lambda: settings)
        self.validator: Singleton[TokenValidator] = Singleton(lambda: build_validator(self.settings()))
        # one shared Gemini client for the LLM and the embedder (only created when Vertex is used)
        self.genai_client: Singleton[Any] = Singleton(self._build_genai_client)
        self.llm: Singleton[LLMProvider] = Singleton(
            lambda: build_llm(self.settings(), self.genai_client() if self.settings().llm_mode == "vertex" else None)
        )
        self.embedder: Singleton[Embedder | None] = Singleton(
            lambda: build_embedder(self.settings(),
                                   self.genai_client() if self.settings().embedder == "vertex" else None)
        )
        self.salesforce: Singleton[SalesforcePort] = Singleton(lambda: build_salesforce(self.settings()))
        self.store: Singleton[RegistryStore] = Singleton(lambda: build_registry_store(self.settings()))
        self.registry: Singleton[AgentRegistry] = Singleton(self._build_registry)
        self.routing: Singleton[RoutingStrategy] = Singleton(self._build_routing)
        self.selector: Singleton[LlmSkillSelector | None] = Singleton(self._build_selector)
        self.supervisor: Singleton[SupervisorAgent] = Singleton(
            lambda: SupervisorAgent(self.registry(), self.routing(), self.llm(), self.selector())
        )
        self.chat: Singleton[HandleChatUseCase] = Singleton(lambda: HandleChatUseCase(self.supervisor()))
        self.skills: Singleton[SkillRegistry] = Singleton(
            lambda: SkillRegistry(self.registry(), self.store(), auto_approve=self.settings().skills_auto_approve,
                                  catalog=self._load_catalog())
        )

    def _build_genai_client(self):
        from infrastructure.llm.vertex import create_genai_client

        s = self.settings()
        return create_genai_client(s.vertex_project, s.vertex_location, s.vertex_api_key, s.llm_timeout_s)

    def _build_selector(self) -> LlmSkillSelector | None:
        s = self.settings()
        if s.skill_selection != "llm":
            return None
        embedder = self.embedder()
        shortlister = SkillShortlister(embedder, s.skill_shortlist_size) if embedder else None
        return LlmSkillSelector(self.skills(), self.llm(), shortlister, s.skill_max_steps)

    def _load_catalog(self):
        s = self.settings()
        directory = Path(s.skills_catalog_dir) if s.skills_catalog_dir else Path(s.agents_config).parent / "skills"
        if s.skills_catalog_dir and not directory.is_dir():
            raise RuntimeError(f"SKILLS_CATALOG_DIR not found: {directory}")
        return load_skill_catalog(directory) if directory.is_dir() else {}

    def _build_registry(self) -> AgentRegistry:
        settings, llm, sf = self.settings(), self.llm(), self.salesforce()
        registry = AgentRegistry()
        for m in self.store().list_agents():
            registry.register(
                McpAgent(
                    name=m.name, description=m.description, required_roles=frozenset(m.required_roles),
                    mcp=build_mcp_client(m, settings, sf), llm=llm, keywords=m.keywords, order=m.order,
                    consumes=m.consumes, produces=m.produces, version=m.version, owner=m.owner, tags=m.tags,
                )
            )
        return registry

    def _build_routing(self) -> RoutingStrategy:
        llm = self.llm()
        keyword, by_llm = KeywordRouting(), LlmRouting(llm)
        by_skill = SkillRouting(self.skills())
        return {
            "keyword": keyword,
            "skill": by_skill,
            "llm": by_llm,
            "hybrid": HybridRouting(keyword, by_skill, by_llm),   # cheapest first, LLM last
        }[self.settings().routing]


@dataclass
class Container:
    settings: Settings
    validator: TokenValidator
    registry: AgentRegistry
    chat: HandleChatUseCase
    skills: SkillRegistry


def build_container(settings: Settings, providers: Providers | None = None) -> Container:
    p = providers or Providers(settings)
    return Container(p.settings(), p.validator(), p.registry(), p.chat(), p.skills())
