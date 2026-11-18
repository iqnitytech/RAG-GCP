# Phase 1: End-to-End Document Ingestion & Linking Workflow

**Parent Index:** [phase1.md](./phase1.md)

---

## 1. End-to-End Document Ingestion Workflow Diagram

### Diagram 2: Phase 1 End-to-End Document Ingestion & Progress Tracking Workflow

```mermaid
flowchart TD
    Start(["Trigger Ingestion<br/>(Shared Path / GCS / PubSub Push / Folder Watcher Poll)"]) --> ValidateApp["Validate & Register application_id<br/>(e.g. CPQ, CustomerExperience)"]
    ValidateApp --> CreateJob["Create IngestionJob in SqliteIngestionStateStore<br/>status = pending -> running (Return HTTP 202)"]
    CreateJob --> Discover["Resolve FolderScanner for Provider<br/>(filesystem | gcs | salesforce | databricks | data_lake | s3 | api_hub)"]
    Discover --> SetDiscovered["Discover & Deduplicate Files<br/>Set job.files_discovered = N"]
    SetDiscovered --> LoopCheck{"Next File in<br/>Discovered Files?"}

    LoopCheck -- "Yes" --> ReadBytes["Download / Read File Bytes<br/>(scanner.read_file)"]
    ReadBytes --> ReadOk{"Read<br/>Succeeded?"}
    ReadOk -- "No" --> RecordErr["service.record_error(job)<br/>files_processed += 1<br/>files_failed += 1"]
    RecordErr --> LoopCheck

    ReadOk -- "Yes" --> ParseDoc["Extract Text via parse_document(data, filename)<br/>(Fallback to VertexGeminiOcr for Images / Scanned PDFs)"]
    ParseDoc --> ParseOk{"Parse<br/>Succeeded?"}
    ParseOk -- "No" --> RecordErr

    ParseOk -- "Yes" --> ComputeIds["Compute Stable document_id = SHA256(app_id + source)<br/>& Content Version doc_id = SHA256(document_id + text)"]
    ComputeIds --> IsDupe{"Version doc_id<br/>Already Indexed?"}

    IsDupe -- "Yes (Unchanged)" --> SkipDoc["Mark skipped = True<br/>files_processed += 1, files_succeeded += 1<br/>documents_skipped += 1"]
    SkipDoc --> LoopCheck

    IsDupe -- "No (New or Updated)" --> ChunkStep["Recursive Chunking (chunk_text)<br/>Paragraph -> Line -> Sentence -> Word with Overlap"]
    ChunkStep --> EmbedStep["Batch Embed Chunks<br/>(VertexEmbedder text-embedding-005 / HashEmbedder)"]
    EmbedStep --> LookupLinks["Fetch Existing linked_document_ids<br/>from SqliteIngestionStateStore"]
    LookupLinks --> UpsertVec["Upsert Deterministic UUIDv5 Points to QdrantVectorStore<br/>with application_id, document_id, linked_document_ids"]
    UpsertVec --> PersistDoc["Save DocumentRecord & Job Counters to SQLite WAL<br/>files_processed += 1, files_succeeded += 1<br/>processed_files.append(file_key)"]
    PersistDoc --> LoopCheck

    LoopCheck -- "No (All Files Done)" --> FinishJob["service.finish(job)<br/>status = completed (or failed if all failed)<br/>Persist finished_at to SQLite WAL"]
```

---

## 2. Step-by-Step Ingestion Lifecycle

1. **Application Scoping (`application_id`):**
   - Every ingestion request carries an `application_id` (such as `CPQ` or `CustomerExperience`).
   - `validate_application_id` normalizes the ID and ensures the application is registered in `SqliteIngestionStateStore`.
2. **Job Creation & Background Scheduling (`HTTP 202 Accepted`):**
   - `service.jobs.create(source_type, application_id)` creates a persistent `IngestionJob` in SQLite WAL with `status="pending"` and schedules `run_ingestion_job` on FastAPI's `BackgroundTasks`.
3. **Provider-Specific Folder Discovery (`files_discovered`):**
   - `resolve_folder_scanner(provider)` selects the appropriate scanner:
     - `filesystem`: Recursively walks `SHARED_FOLDERS_ROOT/<path>` (blocking any `../` path traversal) and filters for supported extensions.
     - `gcs`: Lists objects under the prefix in the GCS document bucket (`StorageService.list_objects`).
     - `salesforce`: Uses `AsyncEdhConnector.list_objects` to find `ContentVersion` documents under the shared path prefix.
     - `data_lake` / `databricks` / `object_storage_hub` / `api_hub`: Uses the SDK's `AsyncEdhConnector.list_objects` for that strategy.
   - Sets `job.files_discovered` to the total number of unique files found.
4. **Fault-Isolated Per-File Processing:**
   - Each file is downloaded and parsed inside its own `try / except` block.
   - If a file is unreadable or corrupt, `service.record_error` increments `files_processed` and `files_failed`, records the error in `job.errors`, saves to SQLite, and continues processing the remaining files.
5. **Format-Aware Parsing & Automatic OCR Fallback:**
   - `parse_document(data, filename, ocr)` extracts plain text from 30+ extensions.
   - Digital PDFs are parsed with `pypdf`; if a PDF contains almost no extractable text (< 20 characters) and `OCR_PROVIDER=vertex`, it automatically falls back to `VertexGeminiOcr`.
   - Images (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff`, `.heic`) are sent to `VertexGeminiOcr`.
6. **Idempotent Deduplication & Stable Document Identity:**
   - **`document_id`**: Hash of `(application_id, source_uri)` — remains constant when a document is re-uploaded with updated content.
   - **`doc_id` (`version_id`)**: Hash of `(document_id, normalized_text)`.
   - If `doc_id` is already present in `SqliteIngestionStateStore`, embedding and Qdrant upsert are skipped (`documents_skipped += 1`, `files_processed += 1`, `files_succeeded += 1`).
7. **Recursive Chunking, Batch Embedding & Vector Indexing:**
   - `chunk_text` splits text recursively by paragraphs -> lines -> sentences -> words into `CHUNK_SIZE` (`1000`) with word-aligned `CHUNK_OVERLAP` (`200`).
   - `embedder.embed_documents(chunks)` generates vectors in batches.
   - Chunks are given deterministic UUIDv5 IDs (`uuid5(NAMESPACE, f"{doc_id}:{chunk_index}")`) and upserted into `QdrantVectorStore` (or `InMemoryVectorStore`) with `application_id`, `document_id`, and any existing `linked_document_ids`.
8. **Live Progress Counters (`files_processed`):**
   - After each file, `record_result` updates `files_processed`, `files_succeeded`, `processed_files`, `documents_indexed`, and `chunks_indexed` in SQLite WAL.

---

## 3. Event-Driven Ingestion (Google Cloud Pub/Sub & Folder Watcher)

1. **Google Cloud Pub/Sub Push (`POST /ingestion/events/pubsub/push`):**
   - Receives standard Pub/Sub push envelopes (`{"message": {"data": "<base64>", "attributes": {...}}, "subscription": "..."}`).
   - Automatically decodes both custom RAG ingestion events and **native GCS Bucket Notification (`OBJECT_FINALIZE`)** messages, infers `application_id` from object metadata or path (`documents/<application_id>/...`), and triggers background ingestion.
2. **Google Cloud Pub/Sub Pull (`POST /ingestion/events/pubsub/pull`):**
   - Pulls messages from `PUBSUB_INGESTION_SUBSCRIPTION`, queues background jobs, and acknowledges (`:acknowledge`) the processed `ackId`s.
3. **Shared-Folder Watcher Subscriptions (`POST /ingestion/events/subscriptions` & `POST /ingestion/events/poll`):**
   - Watches registered `(application_id, provider, shared_path)` folders, hashes file fingerprints, and queues ingestion jobs **only for newly added or modified files** since the previous poll.

---

## 4. Admin Portal Document Dependency Linking

When an admin links two documents in the Admin Portal (`POST /ingestion/documents/{document_id}/links`):
- Validates both documents exist and belong to the **same `application_id`** (blocking cross-application links such as `CPQ` -> `CustomerExperience` with HTTP `400`).
- Blocks self-links, duplicate links, and circular dependencies (`A depends_on B -> B depends_on A`) on directed relation types (`depends_on`, `attachment_of`, `amends`, `supersedes`).
- Persists the link in `SqliteIngestionStateStore` and immediately calls `vector_store.set_links(...)` to update `linked_document_ids` on all vector chunks of both documents.
