from __future__ import annotations

from typing import Any

from domain.ports import Embedder
from settings import Settings

from .hashing import HashingEmbedder


def build_embedder(settings: Settings, genai_client: Any = None) -> Embedder | None:
    if settings.embedder == "none":
        return None
    if settings.embedder == "hash":
        return HashingEmbedder()
    if settings.embedder == "vertex":
        from .vertex import VertexEmbedder

        return VertexEmbedder(genai_client, settings.vertex_model_embedding)
    raise ValueError(f"Unknown EMBEDDER: {settings.embedder}")
