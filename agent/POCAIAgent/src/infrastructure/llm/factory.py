from __future__ import annotations

from typing import Any

from domain.ports import LLMProvider
from settings import Settings

from .mock import MockLLM


def build_llm(settings: Settings, genai_client: Any = None) -> LLMProvider:
    if settings.llm_mode == "mock":
        return MockLLM()
    if settings.llm_mode == "vertex":
        from .vertex import VertexGeminiLLM, VertexModels, create_genai_client

        client = genai_client or create_genai_client(
            settings.vertex_project, settings.vertex_location, settings.vertex_api_key, settings.llm_timeout_s
        )
        models = VertexModels(settings.vertex_model_routing, settings.vertex_model_tools,
                              settings.vertex_model_synthesis)
        return VertexGeminiLLM(client, models)
    raise ValueError(f"Unknown LLM_MODE: {settings.llm_mode}")
