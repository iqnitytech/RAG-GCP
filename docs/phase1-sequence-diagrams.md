# Phase 1: Sequence Diagrams

**Parent Index:** [phase1.md](./phase1.md)

---

## 1. Sequence Diagram 1 — Shared Folder & Background Ingestion Flow

```mermaid
sequenceDiagram
    autonumber
    actor Producer as Admin Portal / API Caller
    participant API as FastAPI Ingestion Router<br/>(/ingestion/shared-paths)
    participant Worker as BackgroundTasks<br/>(run_ingestion_job)
    participant Scanner as FolderScanner<br/>(Filesystem / GCS / EDH SDK)
    participant Parser as Parser & VertexGeminiOcr
    participant Embed as VertexEmbedder / HashEmbedder
    participant Qdrant as QdrantVectorStore
    participant DB as SqliteIngestionStateStore (WAL)

    Producer->>API: POST /ingestion/shared-paths<br/>{application_id: "CPQ", provider: "filesystem", shared_paths: ["cpq/2026"]}
    API->>DB: register_application("CPQ") & save IngestionJob (status="pending")
    API->>Worker: Schedule run_ingestion_job(job, request)
    API-->>Producer: HTTP 202 Accepted {job_id, application_id: "CPQ", status: "pending"}

    Worker->>DB: Update job status = "running"
    Worker->>Scanner: list_files(shared_paths, zone="raw", limit=200)
    Scanner-->>Worker: Return list[DiscoveredFile]
    Worker->>DB: Save job.files_discovered = count

    loop For each DiscoveredFile
        Worker->>Scanner: read_file(item)
        Scanner-->>Worker: Raw file bytes
        Worker->>Parser: parse_document(bytes, filename, ocr)
        Parser-->>Worker: Normalized plain text
        Worker->>DB: has_indexed_version(doc_id)
        alt Version not yet indexed
            Worker->>Embed: embed_documents(chunks)
            Embed-->>Worker: Vector embeddings
            Worker->>DB: linked_ids(document_id)
            DB-->>Worker: Existing linked_document_ids
            Worker->>Qdrant: ensure_collection() & upsert(points)
            Worker->>DB: register(DocumentRecord)
        end
        Worker->>DB: record_result(job) -> files_processed += 1, files_succeeded += 1
    end

    Worker->>DB: finish(job) -> status = "completed", finished_at = timestamp
    Producer->>API: GET /ingestion/jobs/{job_id}
    API->>DB: load_job(job_id)
    API-->>Producer: HTTP 200 {status: "completed", files_discovered, files_processed, files_succeeded, processed_files}
```

---

## 2. Sequence Diagram 2 — Google Cloud Pub/Sub Push (`OBJECT_FINALIZE`) & Pull Subscriber Flow

```mermaid
sequenceDiagram
    autonumber
    participant GCS as GCS Document Bucket
    participant PubSub as Google Cloud Pub/Sub<br/>(Topic: rag-ingestion-events)
    participant API as FastAPI Pub/Sub Endpoints<br/>(/ingestion/events/pubsub/*)
    participant Decoder as decode_pubsub_message<br/>(pubsub.py)
    participant Bus as IngestionEventListener
    participant Worker as BackgroundTasks<br/>(run_ingestion_job)
    participant DB as SqliteIngestionStateStore (WAL)

    Note over GCS,PubSub: Flow A: Native GCS Bucket Notification (Push Subscription)
    GCS->>PubSub: Emit OBJECT_FINALIZE for "documents/CustomerExperience/sla.pdf"
    PubSub->>API: POST /ingestion/events/pubsub/push<br/>{message: {messageId, data: base64(JSON), attributes: {eventType: "OBJECT_FINALIZE"}}}
    API->>Decoder: decode_pubsub_message(envelope.message)
    Decoder-->>API: PubSubReceivedMessage(application_id="CustomerExperience", provider="gcs", paths=["documents/CustomerExperience/sla.pdf"])
    API->>DB: Create IngestionJob (application_id="CustomerExperience")
    API->>Bus: record_event(IngestionEvent, job_id)
    API->>Worker: Schedule run_ingestion_job(job, request)
    API-->>PubSub: HTTP 202 Accepted (Pub/Sub marks message acknowledged)

    Note over PubSub,DB: Flow B: Pull Subscription Worker
    API->>PubSub: POST /v1/projects/{p}/subscriptions/{sub}:pull
    PubSub-->>API: receivedMessages [{ackId, message}]
    API->>Decoder: decode_pubsub_message(message)
    API->>DB: Create IngestionJob
    API->>Worker: Schedule run_ingestion_job(job, request)
    API->>PubSub: POST /v1/projects/{p}/subscriptions/{sub}:acknowledge {ackIds}
```

---

## 3. Sequence Diagram 3 — Admin Portal Document Dependency Linking & Vector Payload Sync

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Admin Portal User
    participant UI as Admin Portal UI<br/>(DocumentLinker.jsx)
    participant API as FastAPI Ingestion Router<br/>(/ingestion/documents/*)
    participant Svc as IngestionService
    participant DB as SqliteIngestionStateStore (WAL)
    participant Qdrant as QdrantVectorStore

    Admin->>UI: Select Application "CPQ"
    UI->>API: GET /ingestion/documents?application_id=CPQ
    API->>DB: list_documents(application_id="CPQ")
    DB-->>API: List of DocumentRecords for CPQ
    API-->>UI: HTTP 200 [{document_id: "docA", filename: "quote.pdf"}, {document_id: "docB", filename: "msa.pdf"}]

    Admin->>UI: Link docA -> docB (relation_type="depends_on")
    UI->>API: POST /ingestion/documents/docA/links<br/>{target_document_id: "docB", relation_type: "depends_on", created_by: "admin@company.com"}
    API->>Svc: link_documents("docA", "docB", "depends_on", "admin@company.com")
    Svc->>DB: add_link("docA", "docB", "depends_on")
    Note over DB: 1. Verify docA & docB exist<br/>2. Verify docA.application_id == docB.application_id ("CPQ")<br/>3. Reject self-link & duplicate link<br/>4. Run directed graph traversal (_reaches) to block cycles
    DB-->>Svc: Created DocumentLink (link_id)

    Svc->>DB: linked_ids("docA") & linked_ids("docB")
    Svc->>Qdrant: set_links("docA", ["docB"]) (POST /collections/{col}/points/payload)
    Svc->>Qdrant: set_links("docB", ["docA"]) (POST /collections/{col}/points/payload)
    Svc-->>API: DocumentLink
    API-->>UI: HTTP 201 Created {link_id, application_id: "CPQ", source_document_id: "docA", target_document_id: "docB"}
```

---

## 4. Sequence Diagram 4 — Shared Folder Watcher Subscription & Delta Polling Flow

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Admin / Scheduler
    participant API as FastAPI Event Endpoints<br/>(/ingestion/events/*)
    participant Listener as IngestionEventListener
    participant Scanner as FolderScanner
    participant Worker as BackgroundTasks<br/>(run_ingestion_job)

    Admin->>API: POST /ingestion/events/subscriptions<br/>{application_id: "CustomerExperience", provider: "filesystem", shared_path: "cx_inbox"}
    API->>Listener: add_subscription(application_id="CustomerExperience", provider="filesystem", shared_path="cx_inbox")
    Listener-->>API: FolderWatchSubscription (subscription_id)
    API-->>Admin: HTTP 201 Created

    Admin->>API: POST /ingestion/events/poll?application_id=CustomerExperience
    API->>Listener: list_subscriptions("CustomerExperience")
    API->>Scanner: list_files(["cx_inbox"]) & read_file(item)
    Scanner-->>API: Files + SHA-256 content fingerprints
    API->>Listener: detect_new_files(subscription_id, fingerprints)
    Listener-->>API: Only new/modified file keys (delta)
    alt New or modified files detected
        API->>Listener: record_event(IngestionEvent("folder.detected"), job_id)
        API->>Worker: Schedule run_ingestion_job for new_keys only
    end
    API-->>Admin: HTTP 200 [IngestionEventResponse]
```
