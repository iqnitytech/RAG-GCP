"""Build the process-wide IngestionService from application settings."""

from __future__ import annotations

from functools import lru_cache

from app.auth.gcp import get_gcp_credentials
from app.core.config import Settings, get_settings
from app.services.ingestion.embedder import Embedder, HashEmbedder, VertexEmbedder
from app.services.ingestion.ocr import OcrProvider, VertexGeminiOcr
from app.services.ingestion.service import IngestionService
from app.services.ingestion.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStore,
)


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider.strip().lower() == "vertex":
        return VertexEmbedder(
            project_id=settings.gcp_project_id,
            region=settings.gcp_region,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            credentials_getter=get_gcp_credentials,
        )
    return HashEmbedder(settings.embedding_dimensions)


def build_vector_store(settings: Settings) -> VectorStore:
    if settings.vector_store.strip().lower() == "qdrant" and settings.qdrant_url:
        return QdrantVectorStore(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            api_key=settings.qdrant_api_key.get_secret_value(),
        )
    return InMemoryVectorStore()


def build_ocr(settings: Settings) -> OcrProvider | None:
    if settings.ocr_provider.strip().lower() == "vertex":
        return VertexGeminiOcr(
            project_id=settings.gcp_project_id,
            region=settings.gcp_region,
            model=settings.ocr_model,
            credentials_getter=get_gcp_credentials,
        )
    return None


def create_ingestion_service(settings: Settings | None = None) -> IngestionService:
    resolved = settings or get_settings()
    return IngestionService(
        embedder=build_embedder(resolved),
        vector_store=build_vector_store(resolved),
        chunk_size=resolved.chunk_size,
        chunk_overlap=resolved.chunk_overlap,
        ocr=build_ocr(resolved),
    )


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    return create_ingestion_service()
