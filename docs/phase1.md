# Phase 1: Enterprise Multi-Application Document Ingestion, Event Listener & Document Dependency Pipeline

**Status:** Implemented and unit-tested to production standards (Backend: **46 passed, 8 skipped** live-credential integration tests; SDK: **54 passed, 10 skipped**).

---

## Phase 1 Documentation Split Index

Phase 1 documentation is organized into four dedicated files in the [`docs/`](./) folder:

1. **[Technical Architecture (`phase1-architecture.md`)](./phase1-architecture.md)**
   - **Diagram 1:** Phase 1 Enterprise RAG Ingestion & Linking Technical Architecture (5-layer Mermaid diagram)
   - Backend Components table (`backend/app/services/ingestion/`)
   - Admin Portal UI Architecture (`portals/admin-portal/`)
   - Supported Document Formats (30+ extensions + `VertexGeminiOcr`)
   - Storage & Vector Payload Schema (`QdrantVectorStore` + `SqliteIngestionStateStore` WAL tables)

2. **[End-to-End Document Ingestion & Linking Workflow (`phase1-end-to-end-ingestion.md`)](./phase1-end-to-end-ingestion.md)**
   - **Diagram 2:** Phase 1 End-to-End Document Ingestion & Progress Tracking Workflow
   - 8-step Ingestion Lifecycle (`application_id` scoping, provider discovery, fault isolation, OCR fallback, SHA-256 deduplication, recursive chunking, vector upsert, and live progress counters)
   - Event-Driven Ingestion (Google Cloud Pub/Sub Push `OBJECT_FINALIZE`, Pull Subscriber, and Shared-Folder Watcher)
   - Admin Portal Document Dependency Linking & Cycle Detection rules

3. **[Sequence Diagrams (`phase1-sequence-diagrams.md`)](./phase1-sequence-diagrams.md)**
   - **Sequence Diagram 1:** Shared Folder & Background Ingestion Flow
   - **Sequence Diagram 2:** Google Cloud Pub/Sub Push (`OBJECT_FINALIZE`) & Pull Subscriber Flow
   - **Sequence Diagram 3:** Admin Portal Document Dependency Linking & Vector Payload Sync
   - **Sequence Diagram 4:** Shared Folder Watcher Subscription & Delta Polling Flow

4. **[Local Run & End-to-End Verification Guide (`phase1-local-run.md`)](./phase1-local-run.md)**
   - Virtual environment activation & `pip install` commands
   - Environment configuration reference (`backend/.env.local`)
   - Uvicorn Backend & Admin Portal UI startup commands
   - Step-by-step PowerShell walkthrough (`CPQ` & `CustomerExperience` shared-folder ingestion, job tracking, document linking, and Pub/Sub publish/pull)
   - Automated `pytest` verification commands
