# Enterprise RAG-GCP Backend — Authentication Architecture

> **Scope:** Full authentication model, system architecture, module design,
> function flow, environment configuration, and complete annotated source code.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Folder Structure](#2-folder-structure)
3. [High-Level Architecture Diagram](#3-high-level-architecture-diagram)
4. [Authentication Model — Design Principles](#4-authentication-model--design-principles)
5. [Authentication Flow Diagram](#5-authentication-flow-diagram)
6. [Module-by-Module Breakdown](#6-module-by-module-breakdown)
7. [Function Call Flow — End-to-End](#7-function-call-flow--end-to-end)
8. [Authentication Decision Tree](#8-authentication-decision-tree)
9. [Environment Variables Reference](#9-environment-variables-reference)
10. [Environment Files](#10-environment-files)
11. [Infrastructure Files](#11-infrastructure-files)
12. [Production Secrets & Hardening](#12-production-secrets--hardening)

---

## 1. Project Overview

This backend is a **FastAPI** application that exposes an **Enterprise Retrieval-Augmented Generation (RAG)** API backed by **Google Cloud Platform (GCP)**.

| Responsibility | Technology |
|---|---|
| REST API layer | FastAPI + Uvicorn |
| GCP Identity & Auth | Workload Identity Federation (WIF) / local key (dev) |
| Document storage | Google Cloud Storage (GCS) |
| AI / Embeddings | Vertex AI |
| Vector database | Qdrant |
| Configuration | Pydantic-Settings + per-environment `.env.local` / `.env.production` |

The **authentication subsystem** is the most critical cross-cutting concern: every GCP service call uses the same credential object resolved once at startup and cached forever.

---

## 2. Folder Structure

```
backend/
├── app/
│   ├── main.py                     # FastAPI app factory & entry point
│   ├── dependencies.py             # FastAPI dependency-injection helpers
│   ├── auth/
│   │   ├── __init__.py             # Public re-export of get_gcp_credentials
│   │   └── gcp.py                  # Core auth engine (WIF / local key)
│   ├── core/
│   │   └── config.py               # Pydantic-Settings: all env-var bindings
│   ├── api/
│   │   └── documents/
│   │       └── documents.py        # POST /documents/upload endpoint
│   └── services/
│       └── storage/
│           └── storage_service.py  # GCS client wrapper
├── secrets/
│   └── wif-credentials.json        # External-account WIF JSON (template)
├── .env.local                      # Local dev config (APP_ENV=development, gitignored)
├── .env.production                 # Prod NON-SECRET config only (gitignored)
├── .env.example                    # Developer template
├── .env.production.example         # Production template
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Container image definition
├── docker-compose.yml              # Local Docker Compose stack
└── cloudrun.yaml                   # Cloud Run deployment manifest
```

---

## 3. High-Level Architecture Diagram

```
+-----------------------------------------------------------+
|              CLIENT (browser / API caller)                |
+---------------------------+-------------------------------+
                            | HTTP Request
                            v
+-----------------------------------------------------------+
|           FastAPI Application  (app/main.py)              |
|                                                           |
|  GET /         -> status (env info only outside prod)     |
|  GET /health   -> {"status": "healthy"}                   |
|  POST /documents/upload -> documents router               |
|                                                           |
|  Startup Validation:                                      |
|    GCP_PROJECT_ID must be set                             |
|    GCS_DOCUMENT_BUCKET must be set                        |
|    Prod: DEBUG=false, WIF, QDRANT_API_KEY injected        |
|    Prod: /docs, /redoc, /openapi.json disabled            |
+---------------------------+-------------------------------+
                            |
               +------------v-----------+
               |  app/dependencies.py   |
               |  get_storage_service_  |
               |  dependency()          |
               +------------+-----------+
                            |
               +------------v----------------------------+
               |  StorageService  (storage_service.py)  |
               |  __init__() -> get_gcp_credentials()   |
               |  upload_file() -> GCS blob upload       |
               +------------+----------------------------+
                            |
               +------------v----------------------------+
               |  GCP Auth Engine  (app/auth/gcp.py)    |
               |                                        |
               |  get_gcp_credentials()  @lru_cache     |
               |    +- _validate_auth_config()          |
               |    +- _normalise_scopes()              |
               |    +- [wif-file] load_from_file()      |
               |    +- [wif-rt]  google_default()       |
               |    +- [local]   load_from_file()       |
               |    +- _apply_impersonation_if_needed() |
               +------------+----------------------------+
                            |
         +------------------+------------------+
         v                  v                  v
+----------------+  +------------------+  +--------------+
| WIF Credential |  | WIF Provider/SA  |  | Local key    |
| File (CI/CD)   |  | (Cloud Run/GKE)  |  | gcloud login |
+-------+--------+  +--------+---------+  +------+-------+
        +-------------------+             +------+
                            |
               +------------v-----------+
               |  google.cloud.storage  |
               |  Client(credentials)   |
               |  blob.upload_from_file |
               +------------+-----------+
                            |
               +------------v-----------+
               |  Google Cloud Storage  |
               |  gs://bucket/documents |
               +------------------------+
```

---

## 4. Authentication Model — Design Principles

### Mode: `wif` — Workload Identity Federation (Production)

WIF lets an **external identity** (GitHub Actions OIDC token, Kubernetes service account, Cloud Run identity) exchange a short-lived token for a Google OAuth2 access token **without storing long-lived service account keys**.

Two sub-paths exist:

| Sub-path | When to use | How it works |
|---|---|---|
| **Credential File** `GCP_WIF_CREDENTIAL_FILE` | CI/CD, Kubernetes | External-account JSON read by `google.auth.load_credentials_from_file()` |
| **Provider + SA** `GCP_WIF_PROVIDER` + `GCP_WIF_SERVICE_ACCOUNT` | Cloud Run, GKE | Runtime-attached WIF identity resolved via `google.auth.default()` |

### Mode: `local` — Service-Account JSON Key (Local Development Only)

By default (`GCP_CREDENTIAL_FILE` empty) local development uses your own `gcloud auth application-default login` identity, so no key file exists on disk. Combine it with `GCP_IMPERSONATE_SERVICE_ACCOUNT` so calls run as a low-privilege dev service account. Optionally, set `GCP_CREDENTIAL_FILE` to a service-account JSON key (relative paths resolve from `backend/`).

> **WARNING: `local` is rejected in `production`, `staging`, `prod` and `stage` environments** (enforced in `_validate_auth_config()`; the production settings validator additionally requires `wif`).

### Optional: Service Account Impersonation

After obtaining base credentials, the module can optionally **impersonate a target service account** via `GCP_IMPERSONATE_SERVICE_ACCOUNT`. This implements least-privilege: CI runners and developers authenticate as themselves but act as a restricted SA with limited IAM roles.

---

## 5. Authentication Flow Diagram

```
Application Startup
       |
       v
get_gcp_credentials()  --- @lru_cache hit ---> return cached credentials
       | (first call only)
       v
get_settings()  loads .env.local (dev) or .env.production (prod, chosen by APP_ENV);
                 real environment variables / injected secrets override file values
       |
       v
_validate_auth_config(settings)
       +- GCP_PROJECT_ID empty?              ---> RuntimeError X
       +- GCP_AUTH_MODE not in {wif, local}?   ---> RuntimeError X
       +- GCP_AUTH_MODE==local AND prod env?   ---> RuntimeError X
       +- GCP_AUTH_MODE==wif AND no config?  ---> RuntimeError X
       | (validation passed)
       v
_normalise_scopes(scopes)
       +- caller passed explicit scopes?  -> use those
       +- GCP_DEFAULT_SCOPES is set?      -> parse comma-separated
       +- fallback                        -> ["...cloud-platform"]
       |
       v
  auth_mode == "wif"?
  |
  +- GCP_WIF_CREDENTIAL_FILE set?
  |     +- file exists?  YES -> load_credentials_from_file()
  |     +- file missing? NO  -> RuntimeError X
  |
  +- GCP_WIF_PROVIDER + GCP_WIF_SERVICE_ACCOUNT?
        +- YES -> google.auth.default()
        +- NO  -> RuntimeError X (caught by validator earlier)
  |
  auth_mode == "local"?
  +---> load GCP_CREDENTIAL_FILE  [only if NOT production]
       |
       v
_apply_impersonation_if_needed(credentials, settings, scopes)
       +- GCP_IMPERSONATE_SA set? -> impersonated_credentials.Credentials(source, target, scopes, lifetime=3600)
       +- not set?                -> return base credentials unchanged
       |
       v
  credentials <---- stored in @lru_cache ----> reused for process lifetime
```

---

## 6. Module-by-Module Breakdown

### 6.1 `app/core/config.py` — Settings & Configuration

**Purpose:** Centralized, type-safe configuration using `pydantic-settings`.

**Key design decisions:**
- `resolve_env_file()` picks `.env.production` when `APP_ENV` is `prod`/`production` (read from the real process environment), otherwise `.env.local`.
- A `model_validator` fails fast in production: `DEBUG` must be false, `GCP_AUTH_MODE` must be `wif`, project/bucket/`QDRANT_URL` required, and `QDRANT_API_KEY` must be injected.
- `qdrant_api_key` is a `SecretStr`, masked in logs and reprs.
- `BACKEND_ROOT` = `Path(__file__).resolve().parents[2]` resolves to the `backend/` directory regardless of where Python is invoked from.
- `AliasChoices("GCP_PROJECT_ID", "gcp_project_id")` means both the env-var spelling and Python attribute spelling map to the same field.
- `case_sensitive=False` in `SettingsConfigDict` means `GCP_PROJECT_ID` and `gcp_project_id` are treated identically.
- `extra="ignore"` prevents Pydantic from raising on unknown env vars (safe for Docker environments).
- `@lru_cache(maxsize=1)` on `get_settings()` means `Settings()` is constructed **exactly once** — the selected env file is parsed only on the first call; all subsequent calls return the same cached object.

**Settings fields:**

| Field | Env Var | Default | Purpose |
|---|---|---|---|
| `app_name` | `APP_NAME` | `"Enterprise RAG API"` | FastAPI title |
| `app_env` | `APP_ENV` | `"development"` | Controls prod guard logic |
| `debug` | `DEBUG` | `False` | Debug logging toggle |
| `gcp_project_id` | `GCP_PROJECT_ID` | `""` | GCP project identifier |
| `gcp_region` | `GCP_REGION` | `"asia-south1"` | GCP region |
| `gcp_auth_mode` | `GCP_AUTH_MODE` | `"wif"` | `"wif"` or `"local"` |
| `gcp_credential_file` | `GCP_CREDENTIAL_FILE` | `""` | Local-dev service-account JSON path |
| `gcp_wif_provider` | `GCP_WIF_PROVIDER` | `""` | WIF pool provider name |
| `gcp_wif_service_account` | `GCP_WIF_SERVICE_ACCOUNT` | `""` | SA email for WIF |
| `gcp_wif_credential_file` | `GCP_WIF_CREDENTIAL_FILE` | `""` | Path to WIF JSON file |
| `gcp_impersonate_service_account` | `GCP_IMPERSONATE_SERVICE_ACCOUNT` | `""` | SA to impersonate |
| `gcp_default_scopes` | `GCP_DEFAULT_SCOPES` | `"https://...cloud-platform"` | OAuth2 scopes |
| `gcs_document_bucket` | `GCS_DOCUMENT_BUCKET` | `""` | GCS bucket name |
| `vertex_ai_enabled` | `VERTEX_AI_ENABLED` | `True` | Toggle Vertex AI |
| `llm_provider` | `LLM_PROVIDER` | `"vertex_ai"` | LLM backend |
| `qdrant_url` | `QDRANT_URL` | `""` | Qdrant vector DB URL |
| `qdrant_api_key` | `QDRANT_API_KEY` | `""` | Qdrant API key (`SecretStr`; prod: Secret Manager) |

---

### 6.2 `app/auth/gcp.py` — GCP Authentication Engine

**Purpose:** Single source of truth for all GCP credential resolution. Provides one public function `get_gcp_credentials()` that every GCP client in the application uses.

#### `_normalise_scopes(scopes)`
- Input: Optional iterable of scope strings or `None`
- Output: Clean `list[str]` of non-empty, stripped scope strings
- Priority: explicit caller arg -> `GCP_DEFAULT_SCOPES` setting -> fallback `cloud-platform`

#### `_is_production_environment(settings)`
- Output: `bool` — `True` if `APP_ENV` is `prod`, `production`, `staging`, or `stage`
- Uses `.strip()` to guard against `"production "` (whitespace) bypassing the check

#### `_validate_auth_config(settings)`
- Validation rules in order:
  1. `GCP_PROJECT_ID` must be non-empty
  2. `GCP_AUTH_MODE` must be `"wif"` or `"local"`
  3. `"local"` rejected if running in production/staging
  4. `"wif"` needs `GCP_WIF_CREDENTIAL_FILE` OR (`GCP_WIF_PROVIDER` + `GCP_WIF_SERVICE_ACCOUNT`)
- All field accesses use `(settings.field or "").strip()` for None-safety

#### `_apply_impersonation_if_needed(credentials, settings, scopes)`
- If `GCP_IMPERSONATE_SERVICE_ACCOUNT` is set: wraps with `impersonated_credentials.Credentials` (1-hour lifetime)
- If not set: returns base credentials unchanged

#### `get_gcp_credentials(scopes=None)` — Public API
- Decorated with `@lru_cache(maxsize=8)` — one cached credential per distinct scope tuple; the default call resolves once per process
- `scopes` must be a `tuple` (not `list`) for lru_cache hashability
- Flow: validate config -> normalise scopes -> branch on auth_mode -> optionally impersonate -> return

---

### 6.3 `app/auth/__init__.py` — Public Surface

Controls what is exported from `app.auth`. Only `get_gcp_credentials` is public; all `_private` helpers stay internal to the module.

---

### 6.4 `app/services/storage/storage_service.py` — GCS Storage Service

**Purpose:** Thin, production-safe wrapper around `google-cloud-storage` scoped to a single bucket.

| Component | Purpose |
|---|---|
| `StorageServiceError` | Custom exception for GCS failures; subclasses `RuntimeError` for selective catching |
| `UploadResult` | Frozen dataclass: `bucket`, `object_name`, `gs_url`. `to_dict()` for JSON responses |
| `StorageService.__init__` | Validates config, calls `get_gcp_credentials()`, builds `storage.Client` + `Bucket` handle |
| `_normalise_destination_name` | Strips `/`, rejects `..` and `//` path patterns |
| `_validate_file_object` | Ensures stream is open, readable, and backed by `io.IOBase` |
| `upload_file` | Normalises inputs, calls `blob.upload_from_file(rewind=True)`, returns `UploadResult` |
| `get_storage_service()` | `@lru_cache` factory; returns singleton `StorageService` |

---

### 6.5 `app/dependencies.py` — FastAPI Dependency Wiring

**Purpose:** FastAPI-compatible dependency functions. Enables test-time overrides via `app.dependency_overrides`.
- `get_app_settings()` → thin wrapper over `get_settings()`
- `get_storage_service_dependency()` → thin wrapper over `get_storage_service()`

---

### 6.6 `app/api/documents/documents.py` — Documents API Router

**Purpose:** `POST /documents/upload` — HTTP input validation, then delegate to StorageService.

Step-by-step upload flow:
1. Validate filename is present
2. Check extension is in `{.pdf, .txt, .docx}`
3. Verify stream has `seek`/`tell` methods
4. Measure file size via `seek(0,2)` + `tell()` + `seek(0)`
5. Reject empty files (size == 0) and oversized files (> 10 MB, HTTP 413)
6. Prefix filename with `uuid4().hex` for unique GCS key
7. Call `storage_service.upload_file(...)` -> stored at `documents/{uuid}_{name}`
8. Return HTTP 201 with filename, size_bytes, and storage metadata
9. `finally` block always closes the stream

Exception mapping: `HTTPException` re-raised | `ValueError` -> HTTP 400 | anything else -> HTTP 500

---

### 6.7 `app/main.py` — FastAPI Application Factory

**Purpose:** Creates and configures the FastAPI app. Factory pattern (`create_app()`) enables testability.

**Startup validation (fail-fast):**
- `GCP_PROJECT_ID` must be set
- `GCS_DOCUMENT_BUCKET` must be set

**Routes:** `GET /` (env info only outside production), `GET /health` (load-balancer probe), Documents router.

**Production hardening:** interactive docs and the OpenAPI schema are disabled when `is_production` is true.

The module-level `app = create_app()` is what Uvicorn references as `app.main:app`.

---

## 7. Function Call Flow — End-to-End

`main.create_app()` → route → `dependencies.get_storage_service_dependency()` → `StorageService()` → `get_gcp_credentials()` → `_validate_auth_config()` → `_normalise_scopes()` → WIF/local load → `_apply_impersonation_if_needed()` → cached credentials → `storage.Client`. See section 5 for the detailed flow.

---

## 8. Authentication Decision Tree

| Environment | `GCP_AUTH_MODE` | Required settings |
|---|---|---|
| Local (`APP_ENV=development`) | `local` | `GCP_CREDENTIAL_FILE` (service-account JSON) |
| Staging / Production | `wif` only (`local` rejected) | `GCP_WIF_PROVIDER` + `GCP_WIF_SERVICE_ACCOUNT`, or `GCP_WIF_CREDENTIAL_FILE` |

---

## 9. Environment Variables Reference

See the settings table in section 6.1. Every variable can be supplied either from the env file or as a real environment variable; the latter always wins.

---

## 10. Environment Files

| File | Selected when | Contents |
|---|---|---|
| `.env.local` | `APP_ENV` unset or anything except `prod`/`production` | Dev config; may reference a local credential file under `secrets/` |
| `.env.production` | `APP_ENV=prod` or `production` | Non-secret config only (project, region, WIF provider, bucket, `QDRANT_URL`) |
| `.env.example` / `.env.production.example` | never loaded | Committed templates |

All `.env.*` files except `.env.example` are gitignored. `docker-compose.yml` loads `.env.local`. `.env.stage` is a legacy file and is **not** loaded by the config.

---

## 11. Infrastructure Files

- **`Dockerfile`** — Python 3.12-slim, runs as non-root user `appuser` (uid 10001).
- **`docker-compose.yml`** — local stack using `.env.local` with live reload.
- **`cloudrun.yaml`** — Cloud Run manifest. Sets `APP_ENV=production` and injects `QDRANT_API_KEY` from Secret Manager (`qdrant-api-key`) via `secretKeyRef`. The runtime service account needs `roles/secretmanager.secretAccessor`.

---

## 12. Production Secrets & Hardening

- **Secrets never live in files in production.** `QDRANT_API_KEY` is stored in Secret Manager and injected as an environment variable by Cloud Run; it overrides any file value.
- **Startup fails fast** if a production secret is missing or unsafe settings are used (see 6.1).
- **No long-lived keys:** production uses WIF; `local` is rejected for staging/production.
- **Reduced surface:** `/docs`, `/redoc` and `/openapi.json` are disabled, and `GET /` does not expose project details in production.
- **Open items:** `backend/secrets/wif-credentials.json` should be removed from git history and mounted as a secret; dependencies are unpinned; the upload endpoint has no authentication yet.

### Local development with impersonation

In \.env.local\, \GCP_IMPERSONATE_SERVICE_ACCOUNT=svc-rag-dev@<project>.iam.gserviceaccount.com\ makes the local key act as a dedicated low-privilege dev service account. Required one-time IAM setup: the identity in \GCP_CREDENTIAL_FILE\ needs \oles/iam.serviceAccountTokenCreator\ on \svc-rag-dev\, and \svc-rag-dev\ needs only the storage/Vertex AI roles the app uses. The IAM Service Account Credentials API must be enabled.

---

## 13. Upload API (production behaviour)

- \POST /documents/upload\ is async; the blocking GCS call runs in a worker thread (\un_in_threadpool\).
- Authentication: header \X-API-Key\ checked in constant time against \API_KEY\ (Secret Manager in production, required at startup).
- Validation: 10 MB limit (Content-Length pre-check plus exact size), extension allow-list, magic-byte check, sanitised object name, content type derived server-side.
- GCS: uploads never overwrite (\if_generation_match=0\), are retry-safe, and time out after 120 s.
- Errors: storage failures return a generic 503 (details only in server logs); no internal messages leak to clients.
- Probes: \/health\ (liveness, no external calls) and \/ready\ (bucket reachable). The GCS client is warmed up at startup (fail-fast in production) and closed on shutdown.

### Upload models layout

```text
app/models/
  request/
    document_upload.py                      # DocumentUploadRequest + FastAPI dependency
    validations/
      document_upload_validation.py         # all upload rules (size, type, magic bytes, filename)
  response/
    document_upload.py                      # DocumentUploadResponse, StorageInfo
```  
Add a new endpoint by adding one request file, one validation file named after it, and one response file.

---

## 14. Shared SDK (`backend/sdk/`)

Framework-level code lives in `sdk/` and never imports from `app/`; the app injects its settings. `app/auth/gcp.py`, `app/core/security.py` and `app/core/rate_limit.py` are now thin bindings to the SDK.

| SDK module | Provides |
|---|---|
| `sdk.auth.gcp` | `build_gcp_credentials(settings, scopes, base_dir)`: the WIF / local / impersonation engine (section 4-5) |
| `sdk.auth.api_key` | `make_api_key_dependency(key_getter)`: constant-time `X-API-Key` check |
| `sdk.schemas` | Generic `ApiResponse[T]`, `Page[T]`, `ErrorResponse`, `HealthResponse`, `PageParams` |
| `sdk.logging` | `configure_logging` (JSON in production), request-id context and filter |
| `sdk.middleware` | `RequestContextMiddleware` (X-Request-ID + access log), `SecurityHeadersMiddleware` |
| `sdk.errors` | `AppError` hierarchy and handlers: clients get public messages plus request id, never internals |
| `sdk.ratelimit` | `RateLimiter(limit_getter)` dependency |
| `sdk.health` | `/health` (liveness) and `/ready` (readiness) routes |
| `sdk.app_factory` | `create_base_app(...)` wires everything above in one call |

`app/main.py` now calls `create_base_app(...)` and only adds app-specific routes and lifespan. `StorageServiceError` subclasses `ServiceUnavailableError`, so it is returned as a generic 503 automatically. The SDK can be reused by another service by copying the folder or installing it with its `pyproject.toml`.
