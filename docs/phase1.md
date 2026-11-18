# Phase 1: Ingestion Pipeline

**Status:** Implemented and unit-tested (backend: 43 passed, 8 skipped live-credential integration tests).
**Not yet verified:** real Qdrant, Vertex AI embeddings and Gemini OCR (adapters are tested against mocked HTTP only).

## Goal
Turn documents from GCS and enterprise sources (Salesforce) into searchable vector chunks.

```
source (GCS / Salesforce) -> parse -> chunk -> embed -> index (Qdrant)
```

## Components (`backend/app/services/ingestion/`)

| File | Responsibility |
|---|---|
| `parsers.py` | Extract text by file extension (see formats below) |
| `ocr.py` | `OcrProvider` protocol + `VertexGeminiOcr` (images, scanned PDFs) |
| `chunker.py` | Recursive splitter (paragraph, line, sentence, word) with overlap |
| `embedder.py` | `HashEmbedder` (dev/test) and `VertexEmbedder` (Vertex AI REST) |
| `vector_store.py` | `InMemoryVectorStore` and `QdrantVectorStore` (REST, with payload link sync) |
| `folder_scanner.py` | Generic `FolderScanner` across `filesystem`, `gcs`, `salesforce`, `databricks`, `data_lake`, `object_storage_hub`, and `api_hub` |
| `registry.py` | `DocumentRegistry` & `InMemoryDocumentRegistry` for Admin Portal document dependency/linking |
| `service.py` | `IngestionService` orchestration, idempotency, `JobStore`, and `link_documents` / `unlink_documents` |
| `factory.py` | Builds the service from settings |

API: [ingestion.py](../backend/app/api/ingestion/ingestion.py), models: [ingestion.py](../backend/app/models/request/ingestion.py).

## Supported formats

| Type | Extensions |
|---|---|
| Text | `.txt .md .markdown .log .rst .ini .cfg .yaml .yml .toml .tex` |
| Tables | `.csv .tsv` (rows rendered as `header: value`) |
| Structured | `.json .jsonl .ndjson .xml` |
| Web / email / RTF | `.html .htm .eml .rtf` |
| Office | `.docx .pptx .xlsx` |
| PDF | `.pdf` (`pypdf`; falls back to OCR when no text is found) |
| Images | `.png .jpg .jpeg .webp .gif .bmp .tif .tiff .heic` (requires OCR) |

Not supported: legacy `.doc/.xls/.ppt`, audio, video, archives.

## API (Multi-Application `application_id` Support)

Every ingestion job, shared folder scan, indexed document, vector chunk, and document link is scoped to an `application_id` (pre-configured with `1. CPQ` and `2. CustomerExperience`, plus dynamic registration via `POST /ingestion/applications`).

### `GET /ingestion/applications` & `POST /ingestion/applications`
List or register application tenants (`CPQ`, `CustomerExperience`, etc.):
```json
{"application_id": "CPQ", "name": "Configure, Price, Quote", "description": "Pricing & quotes"}
```

### `POST /ingestion/jobs` (202)
GCS source:
```json
{"application_id": "CPQ", "source_type": "gcs", "object_names": ["documents/report.pdf"]}
```
Shared Salesforce file paths source (`ContentVersion` folder prefixes processed in the background):
```json
{"source_type": "salesforce_path", "shared_paths": ["shared/policies", "shared/contracts"], "zone": "raw"}
```
Salesforce SObject records source:
```json
{"source_type": "salesforce", "sobject": "Account",
 "text_fields": ["Name", "Description"], "where": {"Industry": "Tech"}, "limit": 200}
```
Returns a job with `job_id` and `status`.

### `POST /ingestion/shared-paths` (202) — Generic Shared Folder Background Ingestion (Any Provider)
Scans one or more shared folders in the background across **any provider** (`filesystem`, `gcs`, `data_lake`, `salesforce`, `databricks`, `object_storage_hub`, `api_hub`, or `memory`), parses and indexes every file, and tracks file-processing progress:
```json
{
  "provider": "filesystem",
  "shared_paths": ["hr/2026", "contracts"],
  "zone": "raw",
  "limit": 200
}
```
(`POST /ingestion/jobs` also accepts `"source_type": "shared_path"` with `"provider": "..."` and `"shared_paths": [...]`, and `POST /ingestion/salesforce/shared-paths` is available as a Salesforce-specific shortcut.)

### `GET /ingestion/jobs` & `GET /ingestion/jobs/{job_id}`
Returns job progress and file-tracking counters:
- `status`: `pending | running | completed | failed`
- `files_discovered`: total files found across the shared paths / request
- `files_processed`: total files processed so far (`files_succeeded + files_failed`)
- `files_succeeded`: files successfully parsed and indexed (or verified unchanged)
- `files_failed`: files that failed downloading or parsing
- `processed_files`: list of file paths successfully processed
- `documents_total`, `documents_indexed`, `documents_skipped`, `chunks_indexed`, `errors`, `created_at`, `finished_at`
Unknown `job_id` returns 404.

### Admin Portal: Document Dependency & Linking Endpoints

When users upload documents and an admin links them in the Admin Portal:
- Each document receives a stable `document_id` (derived from its source path, independent of content re-uploads) alongside a content-specific `version_id` (`doc_id`).
- Admins can link documents with a typed relationship (`depends_on`, `related`, `attachment_of`, `amends`, `supersedes`, `references`).
- Directed relationships (`depends_on`, `attachment_of`, `amends`, `supersedes`) enforce **cycle detection** (rejecting `A -> B -> A` with HTTP 400), as well as self-link and duplicate-link prevention.
- Linking or unlinking immediately updates `linked_document_ids` on all chunks of both documents in the vector store (`set_links`), and re-uploading a new version of a document preserves its existing links.

1. **`GET /ingestion/documents`** — List all indexed documents with their `document_id`, `filename`, `chunk_count`, and `linked_document_ids`.
2. **`POST /ingestion/documents/{document_id}/links` (201)** — Create a link between two documents:
   ```json
   {
     "target_document_id": "a1b2c3d4e5f60718",
     "relation_type": "depends_on",
     "created_by": "admin@company.com"
   }
   ```
3. **`GET /ingestion/documents/{document_id}/links`** — Inspect `outgoing` links, `incoming` links, and `linked_document_ids` for a document.
4. **`DELETE /ingestion/documents/{document_id}/links/{link_id}`** — Remove a link and sync the vector store payloads.

Both job and document-linking endpoints require the `X-API-Key` header (when configured) and are rate limited.

## Behaviour
- **Idempotent:** the document id is a hash of source and content. Chunk ids are deterministic UUIDs, so re-ingesting overwrites instead of duplicating.
- **Failure isolation:** a bad document is recorded in `errors`; the rest of the job continues.
- **Payload stored per chunk:** `application_id, doc_id, document_id, linked_document_ids, source, source_type, chunk_index, chunk_count, text` plus source metadata (`object_name`, `provider`, `shared_path`, `object_key`, `zone`, or `sobject`/`record_id`).

## Configuration (`backend/.env.local`)

| Variable | Default | Meaning |
|---|---|---|
| `EMBEDDING_PROVIDER` | `hash` | `hash` (local) or `vertex` |
| `EMBEDDING_MODEL` | `text-embedding-005` | Vertex model |
| `EMBEDDING_DIMENSIONS` | `768` | Vector size (must match the collection) |
| `VECTOR_STORE` | `memory` | `memory` or `qdrant` |
| `QDRANT_URL` / `QDRANT_API_KEY` | empty | Qdrant endpoint |
| `QDRANT_COLLECTION` | `rag_chunks` | Collection name |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Chunking |
| `OCR_PROVIDER` | `none` | `none` or `vertex` (Gemini) |
| `OCR_MODEL` | `gemini-2.0-flash` | OCR model |
| `SHARED_FOLDERS_ROOT` | `./.shared_folders` | Base root directory for `provider="filesystem"` shared folder scans |
| `SF_SHARED_PATHS` | `shared/documents` | Optional default shared Salesforce folder path prefix |

For real use: `EMBEDDING_PROVIDER=vertex`, `VECTOR_STORE=qdrant`, `QDRANT_URL=...`, and `OCR_PROVIDER=vertex` for images.

## Admin Portal UI (`portals/admin-portal/`)
- **`src/components/ApplicationSelector.jsx`**: Switch between `CPQ`, `CustomerExperience`, or register a new `application_id`.
- **`src/components/SharedFolderIngestionForm.jsx`**: Launch background folder ingestion across any provider (`filesystem`, `gcs`, `data_lake`, `salesforce`, `databricks`, `object_storage_hub`, `api_hub`) for the selected `application_id`.
- **`src/components/JobsTrackerTable.jsx`**: Live file-processing tracker (`files_discovered`, `files_processed`, `files_succeeded`, `files_failed`, `chunks_indexed`, `processed_files`).
- **`src/components/DocumentLinker.jsx`**: Link and unlink documents within the selected `application_id`.

## Supporting changes
- `StorageService.download_bytes()` to read GCS objects.
- `SalesforceService.query()` to read many Salesforce records.
- SDK fix: Salesforce `REQUEST_LIMIT_EXCEEDED` (HTTP 403) is now a rate-limit error, not an auth error.
- `requirements.txt`: added `pypdf`.

## Tests
`backend/tests/unit/test_ingestion.py`: parsers (all formats, OCR, scanned PDF), chunker, idempotent indexing and search, Qdrant and Vertex adapters (mocked HTTP), job API (GCS, Salesforce, validation, 404), and Admin Portal document-linking API (link creation, vector payload sync, cycle detection, self/duplicate link guards, and unlinking).

## Known limitations / follow-ups
1. Job status is in memory (lost on restart, not shared across instances). Persist before production.
2. In-memory dedupe set is per process; Qdrant upserts remain idempotent.
3. `/documents/upload` still has its own narrower file-type allow-list, so new formats reach GCS by other means for now.
4. No ACL/tenant metadata is enforced yet (Phase 3).
5. Run `uvicorn app.main:app` from `backend/` to try it.
