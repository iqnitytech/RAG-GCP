"""Document registry, application registry, and admin-managed links between documents.

Supports multi-application scoping (e.g. ``CPQ``, ``CustomerExperience``) and
admin-defined links between stable ``document_id``s so relationships survive re-uploads.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from sdk.errors import BadRequestError, NotFoundError

DEFAULT_APPLICATION_ID = "CPQ"
DEFAULT_APPLICATIONS: tuple[tuple[str, str, str], ...] = (
    ("CPQ", "Configure, Price, Quote", "Pricing rules, product catalogs, discounting, and quote templates"),
    ("CustomerExperience", "Customer Experience", "Support articles, SLAs, playbooks, and customer onboarding guides"),
)

_SAFE_APP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

RELATION_TYPES = ("related", "depends_on", "attachment_of", "amends", "supersedes", "references")
# Directed relations must not form cycles (A depends_on B depends_on A).
DIRECTED_RELATIONS = {"depends_on", "attachment_of", "amends", "supersedes"}


class DocumentNotFoundError(NotFoundError):
    public_message = "Document not found"


class LinkConflictError(BadRequestError):
    public_message = "Invalid document link"


def validate_application_id(application_id: str | None) -> str:
    """Validate and normalize an ``application_id`` (defaults to ``CPQ`` if empty)."""
    candidate = (application_id or DEFAULT_APPLICATION_ID).strip()
    if not candidate:
        candidate = DEFAULT_APPLICATION_ID
    if not _SAFE_APP_ID.match(candidate):
        raise BadRequestError(
            f"Invalid application_id {application_id!r}",
            public_message="Invalid application_id (use letters, digits, '_', '-', or '.')",
        )
    return candidate


@dataclass(frozen=True)
class ApplicationInfo:
    application_id: str
    name: str
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class DocumentRecord:
    document_id: str
    source: str
    filename: str
    version_id: str  # content-specific id of the latest indexed version
    chunk_count: int
    application_id: str = DEFAULT_APPLICATION_ID
    indexed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class DocumentLink:
    link_id: str
    source_document_id: str
    target_document_id: str
    relation_type: str
    created_by: str
    created_at: str
    application_id: str = DEFAULT_APPLICATION_ID


class DocumentRegistry(Protocol):
    def register_application(
        self, application_id: str, name: str = "", description: str = ""
    ) -> ApplicationInfo: ...

    def list_applications(self) -> list[ApplicationInfo]: ...

    def register(self, record: DocumentRecord) -> None: ...

    def get(self, document_id: str) -> DocumentRecord: ...

    def list_documents(self, application_id: str | None = None) -> list[DocumentRecord]: ...

    def add_link(
        self, source_id: str, target_id: str, relation_type: str, created_by: str = ""
    ) -> DocumentLink: ...

    def remove_link(self, document_id: str, link_id: str) -> DocumentLink: ...

    def links_for(self, document_id: str) -> tuple[list[DocumentLink], list[DocumentLink]]: ...

    def linked_ids(self, document_id: str) -> list[str]: ...


class InMemoryDocumentRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._apps: dict[str, ApplicationInfo] = {
            app_id: ApplicationInfo(application_id=app_id, name=name, description=desc)
            for app_id, name, desc in DEFAULT_APPLICATIONS
        }
        self._docs: dict[str, DocumentRecord] = {}
        self._links: dict[str, DocumentLink] = {}

    def register_application(
        self, application_id: str, name: str = "", description: str = ""
    ) -> ApplicationInfo:
        clean_id = validate_application_id(application_id)
        with self._lock:
            existing = self._apps.get(clean_id)
            if existing is not None and not name and not description:
                return existing
            info = ApplicationInfo(
                application_id=clean_id,
                name=name.strip() or (existing.name if existing else clean_id),
                description=description.strip() or (existing.description if existing else ""),
            )
            self._apps[clean_id] = info
            return info

    def list_applications(self) -> list[ApplicationInfo]:
        with self._lock:
            return list(self._apps.values())

    def register(self, record: DocumentRecord) -> None:
        clean_app = validate_application_id(record.application_id)
        record.application_id = clean_app
        with self._lock:
            if clean_app not in self._apps:
                self._apps[clean_app] = ApplicationInfo(application_id=clean_app, name=clean_app)
            self._docs[record.document_id] = record

    def get(self, document_id: str) -> DocumentRecord:
        with self._lock:
            return self._get(document_id)

    def _get(self, document_id: str) -> DocumentRecord:
        doc = self._docs.get(document_id)
        if doc is None:
            raise DocumentNotFoundError(f"Document {document_id} not found")
        return doc

    def list_documents(self, application_id: str | None = None) -> list[DocumentRecord]:
        clean_app = validate_application_id(application_id) if application_id else None
        with self._lock:
            docs = [
                d
                for d in self._docs.values()
                if clean_app is None or d.application_id == clean_app
            ]
            return sorted(docs, key=lambda d: d.indexed_at, reverse=True)

    def _reaches(self, start: str, goal: str, relation: str) -> bool:
        """True if ``goal`` is reachable from ``start`` following directed ``relation`` links."""
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur == goal:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(
                l.target_document_id
                for l in self._links.values()
                if l.source_document_id == cur and l.relation_type == relation
            )
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
            src_doc = self._get(source_id)
            tgt_doc = self._get(target_id)
            if src_doc.application_id != tgt_doc.application_id:
                raise LinkConflictError(
                    "Cross-application link",
                    public_message=(
                        f"Cannot link documents across different applications "
                        f"({src_doc.application_id} vs {tgt_doc.application_id})"
                    ),
                )
            for l in self._links.values():
                same = (l.source_document_id, l.target_document_id) == (source_id, target_id)
                reverse_related = (
                    relation_type == "related"
                    and (l.source_document_id, l.target_document_id) == (target_id, source_id)
                )
                if l.relation_type == relation_type and (same or reverse_related):
                    raise LinkConflictError("Duplicate link", public_message="These documents are already linked")
            if relation_type in DIRECTED_RELATIONS and self._reaches(target_id, source_id, relation_type):
                raise LinkConflictError(
                    "Cycle", public_message="This link would create a circular dependency"
                )
            link = DocumentLink(
                link_id=uuid.uuid4().hex,
                source_document_id=source_id,
                target_document_id=target_id,
                relation_type=relation_type,
                created_by=created_by,
                created_at=datetime.now(timezone.utc).isoformat(),
                application_id=src_doc.application_id,
            )
            self._links[link.link_id] = link
            return link

    def remove_link(self, document_id: str, link_id: str) -> DocumentLink:
        with self._lock:
            link = self._links.get(link_id)
            if link is None or document_id not in (link.source_document_id, link.target_document_id):
                raise NotFoundError(f"Link {link_id} not found", public_message="Link not found")
            del self._links[link_id]
            return link

    def links_for(self, document_id: str) -> tuple[list[DocumentLink], list[DocumentLink]]:
        """Return ``(outgoing, incoming)`` links for a document."""
        with self._lock:
            self._get(document_id)
            out = [l for l in self._links.values() if l.source_document_id == document_id]
            inc = [l for l in self._links.values() if l.target_document_id == document_id]
        return out, inc

    def linked_ids(self, document_id: str) -> list[str]:
        """All neighbouring document ids (either direction), for retrieval-time expansion."""
        with self._lock:
            ids = {
                l.target_document_id if l.source_document_id == document_id else l.source_document_id
                for l in self._links.values()
                if document_id in (l.source_document_id, l.target_document_id)
            }
        return sorted(ids)
