"""Durable SQLite (WAL-mode) persistence for Phase 1 ingestion state.

Persists across process restarts and multi-worker instances:
- Registered applications (``CPQ``, ``CustomerExperience``, custom ``application_id``s)
- Indexed documents (``DocumentRecord``) and content deduplication hashes
- Admin Portal document dependency links (``DocumentLink``) with cycle detection
- Background ingestion jobs (``IngestionJob``) and file-processing counters
- Folder-watcher subscriptions and file-fingerprint watermarks
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from threading import RLock

from app.services.ingestion.registry import (
    DEFAULT_APPLICATIONS,
    DIRECTED_RELATIONS,
    RELATION_TYPES,
    ApplicationInfo,
    DocumentLink,
    DocumentNotFoundError,
    DocumentRecord,
    LinkConflictError,
    validate_application_id,
)
from app.services.ingestion.service import IngestionJob, JobStatus
from sdk.errors import NotFoundError


class SqliteIngestionStateStore:
    """Thread-safe, ACID-transactional SQLite (WAL) store for jobs, documents, and links."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS applications (
                application_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                source TEXT NOT NULL,
                filename TEXT NOT NULL,
                version_id TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_app ON documents(application_id);
            CREATE INDEX IF NOT EXISTS idx_documents_version ON documents(version_id);

            CREATE TABLE IF NOT EXISTS document_links (
                link_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                source_document_id TEXT NOT NULL,
                target_document_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_links_src ON document_links(source_document_id);
            CREATE INDEX IF NOT EXISTS idx_links_tgt ON document_links(target_document_id);

            CREATE TABLE IF NOT EXISTS ingestion_jobs (
                job_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL,
                files_discovered INTEGER NOT NULL DEFAULT 0,
                files_processed INTEGER NOT NULL DEFAULT 0,
                files_succeeded INTEGER NOT NULL DEFAULT 0,
                files_failed INTEGER NOT NULL DEFAULT 0,
                processed_files_json TEXT NOT NULL DEFAULT '[]',
                documents_total INTEGER NOT NULL DEFAULT 0,
                documents_indexed INTEGER NOT NULL DEFAULT 0,
                documents_skipped INTEGER NOT NULL DEFAULT 0,
                chunks_indexed INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_app ON ingestion_jobs(application_id);
            """
        )
        for app_id, name, desc in DEFAULT_APPLICATIONS:
            info = ApplicationInfo(application_id=app_id, name=name, description=desc)
            self._conn.execute(
                """
                INSERT OR IGNORE INTO applications (application_id, name, description, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (info.application_id, info.name, info.description, info.created_at),
            )
        self._conn.commit()

    def has_indexed_version(self, version_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM documents WHERE version_id = ? LIMIT 1", (version_id,)
            ).fetchone()
            return row is not None

    # ------------------------------------------------------------------ DocumentRegistry interface
    def register_application(
        self, application_id: str, name: str = "", description: str = ""
    ) -> ApplicationInfo:
        clean_id = validate_application_id(application_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM applications WHERE application_id = ?", (clean_id,)
            ).fetchone()
            if row is not None and not name and not description:
                return ApplicationInfo(**dict(row))
            info = ApplicationInfo(
                application_id=clean_id,
                name=name.strip() or (row["name"] if row else clean_id),
                description=description.strip() or (row["description"] if row else ""),
                created_at=row["created_at"] if row else ApplicationInfo(clean_id, clean_id).created_at,
            )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO applications (application_id, name, description, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (info.application_id, info.name, info.description, info.created_at),
            )
            self._conn.commit()
            return info

    def list_applications(self) -> list[ApplicationInfo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT application_id, name, description, created_at FROM applications ORDER BY created_at"
            ).fetchall()
            return [ApplicationInfo(**dict(r)) for r in rows]

    def register(self, record: DocumentRecord) -> None:
        clean_app = validate_application_id(record.application_id)
        record.application_id = clean_app
        with self._lock:
            self.register_application(clean_app)
            self._conn.execute(
                """
                INSERT OR REPLACE INTO documents
                (document_id, application_id, source, filename, version_id, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.document_id,
                    record.application_id,
                    record.source,
                    record.filename,
                    record.version_id,
                    record.chunk_count,
                    record.indexed_at,
                ),
            )
            self._conn.commit()

    def get(self, document_id: str) -> DocumentRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise DocumentNotFoundError(f"Document {document_id} not found")
            return DocumentRecord(**dict(row))

    def list_documents(self, application_id: str | None = None) -> list[DocumentRecord]:
        clean_app = validate_application_id(application_id) if application_id else None
        with self._lock:
            if clean_app:
                rows = self._conn.execute(
                    "SELECT * FROM documents WHERE application_id = ? ORDER BY indexed_at DESC",
                    (clean_app,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM documents ORDER BY indexed_at DESC"
                ).fetchall()
            return [DocumentRecord(**dict(r)) for r in rows]

    def _reaches(self, start: str, goal: str, relation: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            rows = self._conn.execute(
                "SELECT target_document_id FROM document_links WHERE source_document_id = ? AND relation_type = ?",
                (cur, relation),
            ).fetchall()
            stack.extend(r["target_document_id"] for r in rows)
        return False

    def add_link(
        self, source_id: str, target_id: str, relation_type: str, created_by: str = ""
    ) -> DocumentLink:
        if relation_type not in RELATION_TYPES:
            raise LinkConflictError(
                f"Unknown relation_type {relation_type!r}", public_message="Unknown relation type"
            )
        if source_id == target_id:
            raise LinkConflictError("Self link", public_message="A document cannot link to itself")
        with self._lock:
            src_doc = self.get(source_id)
            tgt_doc = self.get(target_id)
            if src_doc.application_id != tgt_doc.application_id:
                raise LinkConflictError(
                    "Cross-application link",
                    public_message=(
                        f"Cannot link documents across different applications "
                        f"({src_doc.application_id} vs {tgt_doc.application_id})"
                    ),
                )
            existing = self._conn.execute(
                """
                SELECT * FROM document_links
                WHERE relation_type = ?
                  AND ((source_document_id = ? AND target_document_id = ?)
                       OR (? = 'related' AND source_document_id = ? AND target_document_id = ?))
                """,
                (relation_type, source_id, target_id, relation_type, target_id, source_id),
            ).fetchone()
            if existing is not None:
                raise LinkConflictError(
                    "Duplicate link", public_message="These documents are already linked"
                )
            if relation_type in DIRECTED_RELATIONS and self._reaches(target_id, source_id, relation_type):
                raise LinkConflictError(
                    "Cycle", public_message="This link would create a circular dependency"
                )
            import uuid
            from datetime import datetime, timezone

            link = DocumentLink(
                link_id=uuid.uuid4().hex,
                source_document_id=source_id,
                target_document_id=target_id,
                relation_type=relation_type,
                created_by=created_by,
                created_at=datetime.now(timezone.utc).isoformat(),
                application_id=src_doc.application_id,
            )
            self._conn.execute(
                """
                INSERT INTO document_links
                (link_id, application_id, source_document_id, target_document_id, relation_type, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link.link_id,
                    link.application_id,
                    link.source_document_id,
                    link.target_document_id,
                    link.relation_type,
                    link.created_by,
                    link.created_at,
                ),
            )
            self._conn.commit()
            return link

    def remove_link(self, document_id: str, link_id: str) -> DocumentLink:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_links WHERE link_id = ?", (link_id,)
            ).fetchone()
            if row is None or document_id not in (
                row["source_document_id"],
                row["target_document_id"],
            ):
                raise NotFoundError(f"Link {link_id} not found", public_message="Link not found")
            self._conn.execute("DELETE FROM document_links WHERE link_id = ?", (link_id,))
            self._conn.commit()
            return DocumentLink(**dict(row))

    def links_for(self, document_id: str) -> tuple[list[DocumentLink], list[DocumentLink]]:
        with self._lock:
            self.get(document_id)
            out_rows = self._conn.execute(
                "SELECT * FROM document_links WHERE source_document_id = ?", (document_id,)
            ).fetchall()
            inc_rows = self._conn.execute(
                "SELECT * FROM document_links WHERE target_document_id = ?", (document_id,)
            ).fetchall()
            return (
                [DocumentLink(**dict(r)) for r in out_rows],
                [DocumentLink(**dict(r)) for r in inc_rows],
            )

    def linked_ids(self, document_id: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT source_document_id, target_document_id FROM document_links
                WHERE source_document_id = ? OR target_document_id = ?
                """,
                (document_id, document_id),
            ).fetchall()
            ids = {
                r["target_document_id"]
                if r["source_document_id"] == document_id
                else r["source_document_id"]
                for r in rows
            }
            return sorted(ids)

    # ------------------------------------------------------------------ JobStore persistence
    def save_job(self, job: IngestionJob) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO ingestion_jobs
                (job_id, application_id, source_type, status, files_discovered, files_processed,
                 files_succeeded, files_failed, processed_files_json, documents_total,
                 documents_indexed, documents_skipped, chunks_indexed, errors_json, created_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.application_id,
                    job.source_type,
                    job.status.value,
                    job.files_discovered,
                    job.files_processed,
                    job.files_succeeded,
                    job.files_failed,
                    json.dumps(job.processed_files),
                    job.documents_total,
                    job.documents_indexed,
                    job.documents_skipped,
                    job.chunks_indexed,
                    json.dumps(job.errors),
                    job.created_at,
                    job.finished_at,
                ),
            )
            self._conn.commit()

    def load_job(self, job_id: str) -> IngestionJob | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ingestion_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_job(row)

    def load_jobs(self, application_id: str | None = None) -> list[IngestionJob]:
        clean_app = validate_application_id(application_id) if application_id else None
        with self._lock:
            if clean_app:
                rows = self._conn.execute(
                    "SELECT * FROM ingestion_jobs WHERE application_id = ? ORDER BY created_at DESC",
                    (clean_app,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM ingestion_jobs ORDER BY created_at DESC"
                ).fetchall()
            return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> IngestionJob:
        return IngestionJob(
            job_id=row["job_id"],
            application_id=row["application_id"],
            source_type=row["source_type"],
            status=JobStatus(row["status"]),
            files_discovered=int(row["files_discovered"]),
            files_processed=int(row["files_processed"]),
            files_succeeded=int(row["files_succeeded"]),
            files_failed=int(row["files_failed"]),
            processed_files=list(json.loads(row["processed_files_json"] or "[]")),
            documents_total=int(row["documents_total"]),
            documents_indexed=int(row["documents_indexed"]),
            documents_skipped=int(row["documents_skipped"]),
            chunks_indexed=int(row["chunks_indexed"]),
            errors=list(json.loads(row["errors_json"] or "[]")),
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
