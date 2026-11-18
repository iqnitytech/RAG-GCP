# Phase 1: Local Run & End-to-End Verification Guide

**Parent Index:** [phase1.md](./phase1.md)

---

## 1. Environment Activation & Dependency Installation

From PowerShell in the repository root (`c:\Ramendra\AI-Learning\IqnityTech\RAG-GCP`):

```powershell
# 1. Activate the virtual environment
.\environemnt\Scripts\Activate.ps1

# 2. Install the vendored rag-platform-sdk wheel and backend dependencies
python -m pip install -r backend\requirements.txt
```

---

## 2. Environment Configuration (`backend/.env.local`)

| Variable | Local Default | Production Value | Purpose |
|---|---|---|---|
| `EMBEDDING_PROVIDER` | `hash` | `vertex` | `hash` (deterministic local embedder) or `vertex` (Vertex AI REST) |
| `EMBEDDING_MODEL` | `text-embedding-005` | `text-embedding-005` | Vertex AI embedding model |
| `EMBEDDING_DIMENSIONS` | `768` | `768` | Vector size |
| `VECTOR_STORE` | `memory` | `qdrant` | `memory` or `qdrant` |
| `QDRANT_URL` / `QDRANT_API_KEY` | `http://localhost:6333` / empty | Qdrant URL & Secret | Qdrant endpoint |
| `QDRANT_COLLECTION` | `rag_chunks` | `rag_chunks` | Qdrant collection name |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | `1000` / `200` | Recursive chunk size and overlap |
| `OCR_PROVIDER` / `OCR_MODEL` | `none` / `gemini-2.0-flash` | `vertex` / `gemini-2.0-flash` | Multimodal OCR for images and scanned PDFs |
| `SHARED_FOLDERS_ROOT` | `./.shared_folders` | Mounted volume path | Root folder for `provider="filesystem"` shared folder scans |
| `PUBSUB_PROVIDER` | `memory` | `gcp` | `memory` (local broker) or `gcp` (Google Cloud Pub/Sub REST v1) |
| `PUBSUB_INGESTION_TOPIC` | `rag-ingestion-events` | `rag-ingestion-events` | Cloud Pub/Sub topic name |
| `PUBSUB_INGESTION_SUBSCRIPTION` | `rag-ingestion-sub` | `rag-ingestion-sub` | Cloud Pub/Sub pull subscription |
| `INGESTION_STATE_DB_PATH` | `./.data_lake/ingestion_state.sqlite3` | Persistent volume DB path | Durable SQLite WAL database for jobs, apps, docs & links |

---

## 3. Start the FastAPI Backend & Admin Portal UI

### A. Start the FastAPI Backend (Terminal 1)
```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
- **Swagger UI:** http://127.0.0.1:8000/docs
- **Health Check:** http://127.0.0.1:8000/health
- **Readiness Check:** http://127.0.0.1:8000/ready

### B. Start the Admin Portal UI (Terminal 2, Optional)
```powershell
cd portals\admin-portal
npm install
npm run dev
```

---

## 4. End-to-End Local Walkthrough (PowerShell Commands)

Run these commands in PowerShell while the backend is running on `http://127.0.0.1:8000`:

### Step 1: List Registered Applications (`CPQ`, `CustomerExperience`)
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/applications" -Method Get
```

### Step 2: Create a Shared Folder & Launch Background Ingestion for `CPQ`
```powershell
mkdir -Force backend\.shared_folders\cpq\2026
Set-Content -Path backend\.shared_folders\cpq\2026\pricing_policy.txt -Value "CPQ Enterprise discount tier requires VP approval for deals above 25 percent."
Set-Content -Path backend\.shared_folders\cpq\2026\msa_terms.txt -Value "Master Services Agreement Appendix B: payment terms are Net-45."

$job = Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/shared-paths" -Method Post -ContentType "application/json" -Body '{"application_id": "CPQ", "provider": "filesystem", "shared_paths": ["cpq/2026"]}'
$job
```

### Step 3: Check File-Processing Progress (`files_discovered`, `files_processed`, `processed_files`)
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/jobs/$($job.job_id)" -Method Get
```

### Step 4: List Indexed Documents for `CPQ` & Link Two Documents
```powershell
$docs = Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/documents?application_id=CPQ" -Method Get
$docs

# Link doc[0] -> doc[1] ("depends_on")
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/documents/$($docs[0].document_id)/links" -Method Post -ContentType "application/json" -Body "{`"target_document_id`": `"$($docs[1].document_id)`", `"relation_type`": `"depends_on`", `"created_by`": `"admin@portal`"}"
```

### Step 5: Test Google Cloud Pub/Sub Publish & Pull Locally
```powershell
# Publish an event to the local Pub/Sub topic
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/events/pubsub/publish" -Method Post -ContentType "application/json" -Body '{"event_type": "file.created", "application_id": "CPQ", "provider": "filesystem", "paths": ["cpq/2026"]}'

# Pull and acknowledge from the Pub/Sub subscription
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ingestion/events/pubsub/pull" -Method Post
```

---

## 5. Run the Automated Test Suites

```powershell
# Run Phase 1 ingestion unit tests
python -m pytest backend/tests/unit/test_ingestion.py -v

# Run full Backend test suite (46 passed, 8 skipped)
python -m pytest backend/tests -v

# Run full SDK test suite (54 passed, 10 skipped)
python -m pytest rag-platform-sdk/tests -v
```
