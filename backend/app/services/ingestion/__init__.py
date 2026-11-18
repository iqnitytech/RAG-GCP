"""Ingestion service package (parse, chunk, embed, index)."""

from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.embedder import Embedder, HashEmbedder, VertexEmbedder
from app.services.ingestion.factory import get_ingestion_service
from app.services.ingestion.registry import (
    RELATION_TYPES,
    DocumentLink,
    DocumentNotFoundError,
    DocumentRecord,
    DocumentRegistry,
    InMemoryDocumentRegistry,
    LinkConflictError,
)
from app.services.ingestion.service import IngestionService, JobStatus, document_id_for
from app.services.ingestion.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorPoint,
    VectorStore,
)

__all__ = [
    "DocumentLink",
    "DocumentNotFoundError",
    "DocumentRecord",
    "DocumentRegistry",
    "Embedder",
    "HashEmbedder",
    "InMemoryDocumentRegistry",
    "InMemoryVectorStore",
    "IngestionService",
    "JobStatus",
    "LinkConflictError",
    "QdrantVectorStore",
    "RELATION_TYPES",
    "VectorPoint",
    "VectorStore",
    "VertexEmbedder",
    "chunk_text",
    "document_id_for",
    "get_ingestion_service",
]
