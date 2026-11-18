"""Request/response models for the multi-application ingestion and document-linking API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.ingestion.registry import DEFAULT_APPLICATION_ID, validate_application_id

SharedFolderProvider = Literal[
    "filesystem",
    "gcs",
    "data_lake",
    "salesforce",
    "databricks",
    "object_storage_hub",
    "api_hub",
    "memory",
]


class ApplicationCreateRequest(BaseModel):
    """Register or update an application tenant (e.g. ``CPQ``, ``CustomerExperience``)."""

    application_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Application ID (e.g. CPQ, CustomerExperience)",
    )
    name: str = Field(default="", description="Display name for the Admin Portal")
    description: str = Field(default="", description="Optional description")

    @model_validator(mode="after")
    def _validate_app_id(self) -> "ApplicationCreateRequest":
        self.application_id = validate_application_id(self.application_id)
        return self


class ApplicationInfoResponse(BaseModel):
    application_id: str
    name: str
    description: str
    created_at: str


class IngestionJobRequest(BaseModel):
    application_id: str = Field(
        default=DEFAULT_APPLICATION_ID,
        description="Application ID (e.g. CPQ, CustomerExperience)",
    )
    source_type: Literal["gcs", "salesforce", "shared_path", "salesforce_path"]

    # gcs (explicit object list)
    object_names: list[str] = Field(default_factory=list, max_length=100)

    # salesforce (SOQL records)
    sobject: str | None = None
    fields: list[str] | None = None
    where: dict[str, str] | None = None
    text_fields: list[str] | None = None
    limit: int = Field(default=200, ge=1, le=2000)

    # shared_path / salesforce_path (any folder across filesystem, GCS, Salesforce, Databricks, Data Lake, S3, API Hub)
    provider: SharedFolderProvider = Field(
        default="filesystem",
        description="Folder provider: filesystem | gcs | data_lake | salesforce | databricks | object_storage_hub | api_hub | memory",
    )
    shared_paths: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="One or more folder or file paths to scan and ingest in the background",
    )
    zone: Literal["landing", "quarantine", "raw", "curated", "gold"] = Field(
        default="raw",
        description="EDH zone when scanning an EDH provider folder",
    )

    @model_validator(mode="after")
    def _check_source(self) -> "IngestionJobRequest":
        self.application_id = validate_application_id(self.application_id)
        if self.source_type == "gcs" and not self.object_names:
            raise ValueError("object_names is required for source_type 'gcs'")
        if self.source_type == "salesforce":
            if not self.sobject:
                raise ValueError("sobject is required for source_type 'salesforce'")
            if not self.text_fields:
                raise ValueError("text_fields is required for source_type 'salesforce'")
        if self.source_type in {"shared_path", "salesforce_path"} and not self.shared_paths:
            raise ValueError(f"shared_paths is required for source_type {self.source_type!r}")
        if self.source_type == "salesforce_path":
            self.provider = "salesforce"
        return self


class SharedPathIngestionRequest(BaseModel):
    """Generic request payload for ``POST /ingestion/shared-paths`` (any folder provider)."""

    application_id: str = Field(
        default=DEFAULT_APPLICATION_ID,
        description="Application ID (e.g. CPQ, CustomerExperience)",
    )
    provider: SharedFolderProvider = Field(
        default="filesystem",
        description="Folder provider: filesystem | gcs | data_lake | salesforce | databricks | object_storage_hub | api_hub | memory",
    )
    shared_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="One or more shared folder paths or prefixes to scan and ingest in the background",
    )
    zone: Literal["landing", "quarantine", "raw", "curated", "gold"] = Field(default="raw")
    limit: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def _validate_app(self) -> "SharedPathIngestionRequest":
        self.application_id = validate_application_id(self.application_id)
        return self


class SalesforceSharedPathIngestionRequest(BaseModel):
    """Request payload for ``POST /ingestion/salesforce/shared-paths``."""

    application_id: str = Field(
        default=DEFAULT_APPLICATION_ID,
        description="Application ID (e.g. CPQ, CustomerExperience)",
    )
    shared_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="One or more shared Salesforce paths to scan and ingest in the background",
    )
    zone: Literal["landing", "quarantine", "raw", "curated", "gold"] = Field(default="raw")
    limit: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def _validate_app(self) -> "SalesforceSharedPathIngestionRequest":
        self.application_id = validate_application_id(self.application_id)
        return self


class IngestionJobResponse(BaseModel):
    job_id: str
    application_id: str
    source_type: str
    status: str
    files_discovered: int
    files_processed: int
    files_succeeded: int
    files_failed: int
    processed_files: list[str]
    documents_total: int
    documents_indexed: int
    documents_skipped: int
    chunks_indexed: int
    errors: list[str]
    created_at: str
    finished_at: str | None = None


class CreateDocumentLinkRequest(BaseModel):
    """Admin Portal request to link a document to another document."""

    target_document_id: str = Field(..., min_length=1, description="Target document ID")
    relation_type: str = Field(
        default="depends_on",
        description="Relationship type: related | depends_on | attachment_of | amends | supersedes | references",
    )
    created_by: str = Field(default="", description="Admin identifier who created the link")


class DocumentLinkResponse(BaseModel):
    link_id: str
    application_id: str
    source_document_id: str
    target_document_id: str
    relation_type: str
    created_by: str
    created_at: str


class DocumentLinksOverviewResponse(BaseModel):
    document_id: str
    application_id: str
    outgoing: list[DocumentLinkResponse]
    incoming: list[DocumentLinkResponse]
    linked_document_ids: list[str]


class IndexedDocumentResponse(BaseModel):
    document_id: str
    application_id: str
    source: str
    filename: str
    version_id: str
    chunk_count: int
    indexed_at: str
    linked_document_ids: list[str]
