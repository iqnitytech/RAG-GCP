"""Ingestion orchestration: parse -> chunk -> embed -> index, plus an in-memory job store."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Mapping

from app.services.ingestion.chunker import chunk_text
from app.services.ingestion.embedder import Embedder
from app.services.ingestion.event_listener import IngestionEventListener
from app.services.ingestion.ocr import OcrProvider
from app.services.ingestion.parsers import normalise_whitespace, parse_document
from app.services.ingestion.pubsub import InMemoryPubSubBroker, PubSubBroker
from app.services.ingestion.registry import (
    DEFAULT_APPLICATION_ID,
    DocumentLink,
    DocumentNotFoundError,
    DocumentRecord,
    DocumentRegistry,
    InMemoryDocumentRegistry,
    validate_application_id,
)
from app.services.ingestion.vector_store import VectorPoint, VectorStore

logger = logging.getLogger("app.ingestion")

_NAMESPACE = uuid.UUID("6f1f2c5e-3a64-4a53-9d0e-2b7c1f0e9a11")


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IngestionJob:
    job_id: str
    source_type: str
    application_id: str = DEFAULT_APPLICATION_ID
    status: JobStatus = JobStatus.PENDING
    files_discovered: int = 0
    files_processed: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    processed_files: list[str] = field(default_factory=list)
    documents_total: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    chunks_indexed: int = 0
    errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None


class JobStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, IngestionJob] = {}

    def create(self, source_type: str, application_id: str = DEFAULT_APPLICATION_ID) -> IngestionJob:
        clean_app = validate_application_id(application_id)
        job = IngestionJob(
            job_id=uuid.uuid4().hex,
            source_type=source_type,
            application_id=clean_app,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, application_id: str | None = None) -> list[IngestionJob]:
        clean_app = validate_application_id(application_id) if application_id else None
        with self._lock:
            jobs = [
                j
                for j in self._jobs.values()
                if clean_app is None or j.application_id == clean_app
            ]
            return sorted(jobs, key=lambda j: j.created_at, reverse=True)


@dataclass(frozen=True)
class DocumentResult:
    doc_id: str
    chunks: int
    skipped: bool = False
    document_id: str = ""
    application_id: str = DEFAULT_APPLICATION_ID


def document_id_for(source: str, application_id: str = DEFAULT_APPLICATION_ID) -> str:
    """Stable id for a document scoped by ``application_id`` (independent of content version)."""
    clean_app = validate_application_id(application_id)
    key = source if clean_app == DEFAULT_APPLICATION_ID else f"{clean_app}::{source}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def content_doc_id(source: str, text: str, application_id: str = DEFAULT_APPLICATION_ID) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"{document_id_for(source, application_id)}-{digest}"


class IngestionService:
    def __init__(
        self,
        *,
        embedder: Embedder,
        vector_store: VectorStore,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        ocr: OcrProvider | None = None,
        registry: DocumentRegistry | None = None,
        pubsub: PubSubBroker | None = None,
    ) -> None:
        self._embedder = embedder
        self._store = vector_store
        self._ocr = ocr
        self.registry: DocumentRegistry = registry or InMemoryDocumentRegistry()
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self.jobs = JobStore()
        self.event_listener = IngestionEventListener()
        self.pubsub: PubSubBroker = pubsub or InMemoryPubSubBroker()
        self._indexed_docs: set[str] = set()
        self._lock = Lock()

    def ingest_text(
        self,
        text: str,
        *,
        source: str,
        metadata: Mapping[str, Any] | None = None,
        filename: str = "",
        application_id: str = DEFAULT_APPLICATION_ID,
    ) -> DocumentResult:
        """Index one document scoped to ``application_id``. Idempotent: identical content is skipped."""
        clean_app = validate_application_id(application_id)
        clean = normalise_whitespace(text)
        doc_id = content_doc_id(source, clean, clean_app)
        document_id = document_id_for(source, clean_app)
        with self._lock:
            if doc_id in self._indexed_docs:
                return DocumentResult(
                    doc_id, 0, skipped=True, document_id=document_id, application_id=clean_app
                )
        chunks = chunk_text(clean, chunk_size=self._chunk_size, overlap=self._chunk_overlap)
        if not chunks:
            return DocumentResult(
                doc_id, 0, skipped=True, document_id=document_id, application_id=clean_app
            )

        vectors = self._embedder.embed_documents(chunks)
        base = dict(metadata or {})
        linked = self.registry.linked_ids(document_id) if self._is_registered(document_id) else []
        points = [
            VectorPoint(
                id=str(uuid.uuid5(_NAMESPACE, f"{doc_id}:{i}")),
                vector=vec,
                payload={
                    **base,
                    "application_id": clean_app,
                    "doc_id": doc_id,
                    "document_id": document_id,
                    "linked_document_ids": linked,
                    "source": source,
                    "chunk_index": i,
                    "chunk_count": len(chunks),
                    "text": chunk,
                },
            )
            for i, (chunk, vec) in enumerate(zip(chunks, vectors))
        ]
        self._store.ensure_collection(self._embedder.dimensions)
        self._store.upsert(points)
        self.registry.register(
            DocumentRecord(
                document_id=document_id,
                source=source,
                filename=filename or source.rsplit("/", 1)[-1],
                version_id=doc_id,
                chunk_count=len(points),
                application_id=clean_app,
            )
        )
        with self._lock:
            self._indexed_docs.add(doc_id)
        logger.info(
            "Indexed document -- app=%s source=%s doc_id=%s chunks=%d",
            clean_app,
            source,
            doc_id,
            len(points),
        )
        return DocumentResult(
            doc_id, len(points), document_id=document_id, application_id=clean_app
        )

    def _is_registered(self, document_id: str) -> bool:
        try:
            self.registry.get(document_id)
            return True
        except DocumentNotFoundError:
            return False

    # ------------------------------------------------------------------ admin links
    def _sync_links(self, document_id: str) -> None:
        self._store.set_links(document_id, self.registry.linked_ids(document_id))

    def link_documents(
        self, source_id: str, target_id: str, relation_type: str, created_by: str = ""
    ) -> DocumentLink:
        link = self.registry.add_link(source_id, target_id, relation_type, created_by)
        self._sync_links(source_id)
        self._sync_links(target_id)
        logger.info(
            "Linked documents -- %s -[%s]-> %s by=%s", source_id, relation_type, target_id, created_by or "-"
        )
        return link

    def unlink_documents(self, document_id: str, link_id: str) -> DocumentLink:
        link = self.registry.remove_link(document_id, link_id)
        self._sync_links(link.source_document_id)
        self._sync_links(link.target_document_id)
        logger.info("Unlinked documents -- link_id=%s", link_id)
        return link

    def ingest_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        source: str,
        metadata: Mapping[str, Any] | None = None,
        application_id: str = DEFAULT_APPLICATION_ID,
    ) -> DocumentResult:
        return self.ingest_text(
            parse_document(data, filename, self._ocr),
            source=source,
            metadata=metadata,
            filename=filename,
            application_id=application_id,
        )

    def record_result(
        self,
        job: IngestionJob,
        result: DocumentResult,
        *,
        file_path: str = "",
    ) -> None:
        job.documents_total += 1
        job.files_processed += 1
        job.files_succeeded += 1
        if file_path:
            job.processed_files.append(file_path)
        if result.skipped:
            job.documents_skipped += 1
        else:
            job.documents_indexed += 1
            job.chunks_indexed += result.chunks

    def record_error(self, job: IngestionJob, source: str, exc: Exception) -> None:
        job.documents_total += 1
        job.files_processed += 1
        job.files_failed += 1
        job.errors.append(f"{source}: {type(exc).__name__}")
        logger.error("Ingestion failed for %s: %s", source, exc)

    def finish(self, job: IngestionJob) -> None:
        job.status = (
            JobStatus.FAILED
            if job.errors and job.documents_indexed == 0 and job.documents_skipped == 0
            else JobStatus.COMPLETED
        )
        job.finished_at = datetime.now(timezone.utc).isoformat()

    def close(self) -> None:
        self._store.close()
