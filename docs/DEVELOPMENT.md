# Developer Guide (`RAG-GCP`)

This guide explains how to set up your local environment, configure GCP authentication, run the backend and test suites, update `rag-platform-sdk`, and deploy.

---

## 1. Prerequisites

- **Python 3.12+**
- **Google Cloud SDK (`gcloud`)** for local Application Default Credentials (ADC)
- **Git**
- **Docker** (optional, for container builds and local emulators)
- **Terraform >= 1.5** (optional, for `infra/terraform/`)

---

## 2. Environment Configuration

The backend selects its dotenv file based on `APP_ENV` (see `backend/app/core/config.py`):
- `APP_ENV=development` (default) -> loads `backend/.env.local`
- `APP_ENV=prod` or `production` -> loads `backend/.env.production`
- Real environment variables (for example, Cloud Run secrets mounted from Secret Manager) always override values in dotenv files.

### Setting up `.env.local`
```powershell
cd backend
Copy-Item .env.example .env.local
```

Key settings in `backend/.env.local`:
- `GCP_AUTH_MODE=local`: Uses your local `gcloud auth application-default login` session (or a key file if `GCP_CREDENTIAL_FILE` is set). Rejected in production.
- `GCP_IMPERSONATE_SERVICE_ACCOUNT`: Optional service account email to impersonate locally so your local calls exercise the same IAM permissions as Cloud Run.
- `API_KEY`: Shared secret expected in the `X-API-Key` header. If empty in development, the header check is disabled; in production, startup fails if `API_KEY` or `QDRANT_API_KEY` is missing.
- `RATE_LIMIT_PER_MINUTE`: Sliding-window request limit per caller (default `60`; `0` disables). Failed API-key attempts are always rate-limited per client IP before key comparison.

### Secrets Hygiene & Historical Note (H-6 / H-7)
- Never store real API keys, Qdrant keys, or service-account JSON keys in `.env.*` files or commit them to git.
- Production secrets (`API_KEY`, `QDRANT_API_KEY`) must be stored in **Google Cloud Secret Manager** and injected as runtime environment variables.
- **Git history audit (`H-6`):** `backend/secrets/wif-credentials.json` (committed in `722cf739` and untracked in `eb278f6`) was verified to be an external-account WIF configuration template containing only placeholder identifiers (`123456789012`, `my-pool`, `your-gcp-project-id`) and zero private keys or secret material.

---

## 3. Installing & Running Locally

From `backend/`:

```powershell
# Install runtime + development dependencies (resolves rag-platform-sdk from ./vendor)
pip install -r requirements-dev.txt

# Authenticate with GCP for local storage access
gcloud auth application-default login

# Start the FastAPI server
uvicorn app.main:app --reload --port 8000
```

### Example Upload Request
```powershell
curl.exe -X POST "http://localhost:8000/documents/upload" `
  -H "X-API-Key: <your-dev-key-if-set>" `
  -F "file=@sample.pdf"
```

---

## 4. Running Tests & Quality Checks

### Backend Tests
```powershell
python -m pytest backend -v
```

### SDK Tests
From the repository root:
```powershell
python -m pytest rag-platform-sdk -v
```

### Linting, Type Checking, and Security Scans
Once dev tools from `backend/requirements-dev.txt` are installed:
```powershell
cd backend
python -m ruff check app tests
python -m mypy app
python -m bandit -r app
python -m pytest --cov=app
```

---

## 5. Releasing & Updating `rag-platform-sdk`

The backend enforces a compatible SDK version range at startup in `backend/app/main.py`:
```python
require_sdk_version(minimum="0.2.0", below="0.3.0")
```

When making changes in `rag-platform-sdk/`:
1. Update `sdk/_version.py` (following Semantic Versioning) and document changes in `CHANGELOG.md`.
2. Run the SDK test suite (`python -m pytest rag-platform-sdk`).
3. Build and publish the wheel into `backend/vendor/`:
   ```powershell
   .\rag-platform-sdk\scripts\release.ps1
   ```
4. If the minor version changed (while `0.x`), update `backend/requirements.txt` and `require_sdk_version(...)` in `backend/app/main.py`, reinstall the wheel, and run backend tests.

---

## 6. Deployment Overview

1. **Infrastructure (`infra/terraform/`)**: Provisions required GCP APIs, Artifact Registry, GCS buckets, least-privilege service accounts, and IAM bindings.
2. **Container Build (`backend/Dockerfile`)**: Builds a non-root production image (`appuser`, UID `10001`) installing pinned dependencies and the vendored SDK wheel, running Uvicorn with `--proxy-headers`.
   ```powershell
   cd backend
   docker build -t rag-gcp-backend:local .
   ```
3. **Cloud Run (`backend/cloudrun.yaml`)**: Runs the container using Workload Identity (`GCP_AUTH_MODE=wif`) and injects `API_KEY` and `QDRANT_API_KEY` from Secret Manager.
