# RAG-GCP — Enterprise RAG Platform on Google Cloud

Enterprise-grade Retrieval Augmented Generation (RAG) platform built on **FastAPI**, **Google Cloud Platform** (Cloud Run, Cloud Storage, Vertex AI, Secret Manager, Workload Identity Federation), and **Qdrant**, backed by a shared platform SDK (`rag-platform-sdk`).

## Repository Layout

| Path | Description |
|---|---|
| [`backend/`](backend/) | FastAPI backend service (`app` package), tests, Dockerfile, and vendored SDK wheel (`backend/vendor/`) |
| [`rag-platform-sdk/`](rag-platform-sdk/) | Shared platform SDK source (`sdk/`), tests (`tests/`), changelog, and release script (`scripts/release.ps1`) |
| [`infra/terraform/`](infra/terraform/) | Terraform infrastructure definitions for GCP resources, service accounts, and least-privilege IAM |
| [`portals/`](portals/) | Frontend portal workspaces (`admin-portal`, `orchestration-portal`, `retrieval-portal`) |
| [`docs/`](docs/) | Target architecture ([`TARGET_ARCHITECTURE.md`](docs/TARGET_ARCHITECTURE.md)), step-by-step build plan ([`TASK_LIST.md`](docs/TASK_LIST.md)), and developer guide ([`DEVELOPMENT.md`](docs/DEVELOPMENT.md)) |
| [`tests/`](tests/) | Repository-level end-to-end and smoke tests |
| [`scripts/`](scripts/) | Developer and operational utility scripts |

> **Shared SDK:** Cross-cutting concerns (GCP WIF/ADC auth, API-key verification with brute-force failure throttling, sliding-window rate limiting, body-size limit middleware, structured JSON logging with `X-Request-ID`, error handlers, health/readiness probes, and version guards) live in [`rag-platform-sdk/`](rag-platform-sdk/) and are consumed by `backend/` via `backend/vendor/*.whl` (or Artifact Registry).

## Quick Start

1. **Install dependencies** (Python 3.12+):
   ```powershell
   cd backend
   pip install -r requirements-dev.txt
   ```
2. **Configure local environment**:
   ```powershell
   Copy-Item .env.example .env.local
   gcloud auth application-default login
   ```
3. **Run tests**:
   ```powershell
   python -m pytest -v
   ```
4. **Start the API server**:
   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```
   Open `http://localhost:8000/docs` (non-production only) or probe `http://localhost:8000/health` and `http://localhost:8000/ready`.

## Documentation

- **[Developer Guide (`docs/DEVELOPMENT.md`)](docs/DEVELOPMENT.md)** — Prerequisites, environment files, authentication modes, running tests, SDK release flow, and deployment overview.
- **[EDH Connector Architecture (`docs/EDH_CONNECTOR_ARCHITECTURE.md`)](docs/EDH_CONNECTOR_ARCHITECTURE.md)** — Strategy-Pattern Enterprise Data Hub connector (`sdk.edh`) for Data Lakes, Object Storage Hubs, and API-Driven Hubs using Python core libraries.
- **[Target Architecture (`docs/TARGET_ARCHITECTURE.md`)](docs/TARGET_ARCHITECTURE.md)** — Hexagonal architecture, ingestion & online planes, security controls, and production readiness gates.
- **[Task List (`docs/TASK_LIST.md`)](docs/TASK_LIST.md)** — Phased implementation checklist and review findings.
- **[Authentication Architecture (`backend/AUTH_ARCHITECTURE.md`)](backend/AUTH_ARCHITECTURE.md)** — Detailed GCP WIF, ADC, impersonation, and API-key security reference.
