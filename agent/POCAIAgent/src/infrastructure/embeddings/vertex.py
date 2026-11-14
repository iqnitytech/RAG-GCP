from __future__ import annotations

from typing import Any, Sequence

from domain.errors import UpstreamError


class VertexEmbedder:
    """Vertex AI text embeddings (shared google-genai client; ADC or API key)."""

    def __init__(self, client: Any, model: str) -> None:
        self._client, self._model = client, model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            res = await self._client.aio.models.embed_content(model=self._model, contents=list(texts))
        except Exception as exc:
            raise UpstreamError("Embedding call failed") from exc
        return [list(e.values) for e in res.embeddings]
