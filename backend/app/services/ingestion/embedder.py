"""Embedding providers: deterministic hash (dev/test) and Vertex AI (REST)."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol, Sequence

import httpx
from sdk.errors import ServiceUnavailableError

logger = logging.getLogger("app.ingestion.embedder")


class EmbeddingError(ServiceUnavailableError):
    public_message = "Embedding service is temporarily unavailable"


class Embedder(Protocol):
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. For local dev and tests only."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for token in re.findall(r"\w+", text.lower()):
            h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")
            vec[h % self.dimensions] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VertexEmbedder:
    """Vertex AI text embeddings via the REST ``:predict`` endpoint."""

    MAX_BATCH = 20

    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        model: str = "text-embedding-005",
        dimensions: int = 768,
        credentials_getter=None,  # noqa: ANN001
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.dimensions = dimensions
        self._url = (
            f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}"
            f"/locations/{region}/publishers/google/models/{model}:predict"
        )
        self._credentials_getter = credentials_getter
        self._timeout = timeout_seconds
        self._client = http_client

    def _token(self) -> str:
        if self._credentials_getter is None:
            raise EmbeddingError("No GCP credentials configured for Vertex embeddings")
        from google.auth.transport.requests import Request  # lazy import

        creds = self._credentials_getter()
        if not creds.valid:
            creds.refresh(Request())
        return creds.token

    def _predict(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        body = {
            "instances": [{"content": t, "task_type": task_type} for t in texts],
            "parameters": {"outputDimensionality": self.dimensions},
        }
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            try:
                resp = client.post(self._url, json=body, headers=headers)
            finally:
                if self._client is None:
                    client.close()
        except httpx.HTTPError as exc:
            logger.error("Vertex embedding request failed: %s", exc)
            raise EmbeddingError(f"Vertex embedding request failed: {exc}") from exc
        if resp.status_code != 200:
            logger.error("Vertex embedding HTTP %s: %s", resp.status_code, resp.text[:300])
            raise EmbeddingError(f"Vertex embedding failed with HTTP {resp.status_code}")
        preds = resp.json().get("predictions", [])
        return [p["embeddings"]["values"] for p in preds]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.MAX_BATCH):
            out.extend(self._predict(texts[i : i + self.MAX_BATCH], "RETRIEVAL_DOCUMENT"))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._predict([text], "RETRIEVAL_QUERY")[0]
