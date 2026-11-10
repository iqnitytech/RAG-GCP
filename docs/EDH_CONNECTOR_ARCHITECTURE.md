# Enterprise Data Hub (EDH) Connector Architecture

Source code:
- Connectors & strategies: [`rag-platform-sdk/sdk/connectors/edh/`](../rag-platform-sdk/sdk/connectors/edh/)
- EDH models: [`rag-platform-sdk/sdk/models/edh_models/`](../rag-platform-sdk/sdk/models/edh_models/)
- Service models: [`rag-platform-sdk/sdk/models/service_models/`](../rag-platform-sdk/sdk/models/service_models/)
- Backend connector wiring: [`backend/app/connectors/`](../backend/app/connectors/)
- Unit & contract tests: [`rag-platform-sdk/tests/unit/test_edh.py`](../rag-platform-sdk/tests/unit/test_edh.py)
- Live HTTP & disk integration tests: [`rag-platform-sdk/tests/integration/test_edh_integration.py`](../rag-platform-sdk/tests/integration/test_edh_integration.py)

---

## 1. Design Goals & Principles

The **Enterprise Data Hub (EDH) Connector** in `rag-platform-sdk` provides a unified, vendor-neutral abstraction for reading and writing unstructured documents and structured datasets across **Data Lakes**, **Object Storage Hubs**, and **API-Driven Data Hubs** using **only Python standard library modules** (`urllib.request`, `ssl`, `hmac`, `hashlib`, `json`, `csv`, `gzip`, `xml.etree.ElementTree`, `pathlib`, `dataclasses`, `threading`).

### SOLID Compliance

| Principle | How `sdk.edh` Enforces It |
|---|---|
| **S**ingle Responsibility | `EdhConnector` owns resilience (circuit breaker + retry), telemetry logging, and input normalization; each strategy (`DataLakeStrategy`, `ObjectStorageHubStrategy`, `ApiDrivenEdhStrategy`, `InMemoryEdhStrategy`) translates to a single backend protocol; `_serde.py` handles record serialization; `_http.py` handles core-library HTTP and S3 SigV4 signing. |
| **O**pen / Closed | New data hub providers are added by registering a strategy factory in [`EdhStrategyRegistry`](../rag-platform-sdk/sdk/edh/registry.py) without modifying [`EdhConnector`](../rag-platform-sdk/sdk/edh/connector.py) or application use cases. Dynamic arbitrary imports are disallowed. |
| **L**iskov Substitution | Every strategy implements [`EdhStrategyPort`](../rag-platform-sdk/sdk/edh/ports.py) and is verified against the same parametrized contract test suite ([`test_edh.py`](../rag-platform-sdk/tests/test_edh.py)) covering `put_bytes`, `get_bytes`, `stream_chunks`, `head`, `list_objects` pagination, `delete` idempotency, `overwrite=False` conflict handling, and partitioned `write_records` / `read_records`. |
| **I**nterface Segregation | Role-based `Protocol` interfaces in [`sdk/edh/ports.py`](../rag-platform-sdk/sdk/edh/ports.py): `EdhReadStrategyPort` (read-only consumers), `EdhWriteStrategyPort` (blob writers), `EdhRecordStrategyPort` (structured dataset readers/writers), and `EdhLifecyclePort` (`ping` / `close`). |
| **D**ependency Inversion | Application services depend only on `EdhReadStrategyPort` / `EdhStrategyPort` or `EdhConnector`; credentials (`auth_token_getter`, `api_key_getter`, `access_key_getter`, `secret_key_getter`) and runtime settings are injected via [`EdhConnectorConfig`](../rag-platform-sdk/sdk/edh/models.py). |

---

## 2. Recommended Architecture (Strategy Pattern)

```mermaid
flowchart TB
    subgraph App["Consuming Microservice (Ingestion Worker / Retrieval API)"]
        UC["Application Use Case"]
    end

    subgraph SDK["rag-platform-sdk (sdk.edh)"]
        CONN["EdhConnector (Context)<br/>- Path traversal & URI validation<br/>- CircuitBreaker (CLOSED/OPEN/HALF_OPEN)<br/>- Exponential backoff + jitter retry<br/>- X-Request-ID & latency telemetry"]
        REG["EdhStrategyRegistry<br/>(Explicit Allowlist)"]
        PORT["<<Protocol>><br/>EdhStrategyPort<br/>(Read / Write / Record / Lifecycle)"]

        S_LAKE["DataLakeStrategy<br/>(pathlib, os.replace, json, csv, gzip, hashlib)"]
        S_OBJ["ObjectStorageHubStrategy<br/>(urllib.request, ssl, hmac SigV4 / WIF Bearer, xml.etree)"]
        S_API["ApiDrivenEdhStrategy<br/>(urllib.request, json, X-Request-ID, Idempotency-Key)"]
        S_MEM["InMemoryEdhStrategy<br/>(Contract-tested test double)"]
    end

    subgraph Backends["Enterprise Data Hub Backends"]
        B_LAKE[("Enterprise Data Lake<br/>Medallion Zones & Hive Partitions<br/>landing / quarantine / raw / curated / gold")]
        B_OBJ[("Object Storage Hub<br/>GCS XML / AWS S3 / MinIO / Ceph")]
        B_API[("API-Driven Data Hub<br/>REST / Governance Gateway")]
    end

    UC --> CONN
    CONN --> REG
    CONN --> PORT
    PORT <|.. S_LAKE
    PORT <|.. S_OBJ
    PORT <|.. S_API
    PORT <|.. S_MEM
    S_LAKE --> B_LAKE
    S_OBJ --> B_OBJ
    S_API --> B_API
```

---

## 3. Core-Library Strategies Overview

### 3.1 `DataLakeStrategy` (`strategy="data_lake"`)
- **Use case:** Mounted enterprise file shares, Cloud Run GCS FUSE mounts, local/staging Medallion data lakes.
- **Python Core Modules:** `pathlib`, `os.replace`, `json`, `csv`, `gzip`, `hashlib`.
- **Key Features:**
  - Enforces Medallion zones (`landing`, `quarantine`, `raw`, `curated`, `gold`).
  - Writes Hive-partitioned datasets (`<zone>/<dataset>/tenant_id=t1/lang=en/part-<hash>.jsonl[.gz]`).
  - Uses atomic temp-file writes followed by `os.replace` so readers never observe partial files.
  - Persists `.edhmeta.json` sidecars and verifies SHA-256 digests on every read and chunked stream.

### 3.2 `ObjectStorageHubStrategy` (`strategy="object_storage_hub"`)
- **Use case:** S3/GCS/MinIO/Ceph-compatible Object Storage Hubs over HTTPS without heavy vendor SDK dependencies.
- **Python Core Modules:** `urllib.request`, `ssl`, `hmac`, `hashlib`, `xml.etree.ElementTree`, `urllib.parse`.
- **Key Features:**
  - Supports **AWS Signature Version 4 (`AWS4-HMAC-SHA256`)** via `access_key_getter` / `secret_key_getter` AND **OAuth2 / GCP WIF Bearer tokens** via `auth_token_getter`.
  - Enforces `If-None-Match: *` when `overwrite=False` (maps HTTP `409`/`412` to `EdhConflictError`).
  - Parses S3 ListObjectsV2 XML responses with continuation tokens for paginated listing and partition pruning.

### 3.3 `ApiDrivenEdhStrategy` (`strategy="api_hub"`)
- **Use case:** Enterprise Data Hub REST gateways that broker access, governance, and lineage over HTTP/JSON and binary streams.
- **Python Core Modules:** `urllib.request`, `ssl`, `json`, `hashlib`.
- **Key Features:**
  - Automatically propagates `X-Request-ID` from `sdk.logging.context` for distributed tracing.
  - Injects `Authorization: Bearer <token>`, `X-API-Key`, `Idempotency-Key`, and `X-EDH-SHA256` headers.
  - Maps upstream HTTP `401/403` → `EdhAuthError`, `404` → `EdhNotFoundError`, `409/412` → `EdhConflictError`, `429` (`Retry-After`) → `EdhRateLimitError`, and `5xx` → `EdhTransientError`.

### 3.4 `SalesforceEdhStrategy` (`strategy="salesforce"` / `"sfdc"` / `"salesforce_crm"`)
- **Use case:** Direct Salesforce CRM / Knowledge / Files (`ContentVersion`) integration over REST & SOQL without third-party dependencies (`simple-salesforce` not required), or federated via `data_lake` / `api_hub`.
- **Python Core Modules:** `urllib.request`, `ssl`, `json`, `base64`, `hashlib`, `urllib.parse`.
- **Key Features:**
  - **Blob storage (`ContentVersion`):** Maps `put_bytes`, `get_bytes`, `stream_chunks`, `head`, `delete`, and `list_objects` to `/services/data/<version>/sobjects/ContentVersion` (storing `EdhObjectRef` path in `PathOnClient`, SHA-256 + custom metadata in `Description` JSON, and payload in `VersionData`).
  - **Structured SObject records (`write_records` / `read_records`):** Uses Composite sObjects Collections (`POST /services/data/<version>/composite/sobjects`) and SOQL queries (`GET /services/data/<version>/query?q=...`) with automatic `nextRecordsUrl` pagination and strict identifier/literal escaping against SOQL injection.
  - **OAuth2 helper (`make_salesforce_oauth_token_getter`):** Thread-safe `client_credentials` token exchange against `<instance_url>/services/oauth2/token` with in-memory TTL caching.

### 3.5 `InMemoryEdhStrategy` (`strategy="memory"`)
- **Use case:** Fast, deterministic unit and contract testing without network or filesystem I/O.

---

## 4. Usage Examples

### 4.1 Selecting a Strategy via Configuration
```python
from sdk.edh import (
    DataZone,
    EdhConnector,
    EdhConnectorConfig,
    EdhRecordBatch,
    EdhWriteOptions,
    RecordFormat,
)

# Example 1: API-Driven Data Hub with WIF / OAuth2 token getter
config = EdhConnectorConfig(
    strategy="api_hub",  # or "data_lake", "object_storage_hub", "memory"
    namespace="enterprise-rag-hub",
    endpoint_url="https://edh.internal.example.com",
    auth_token_getter=lambda: get_wif_access_token(),
    timeout_seconds=10.0,
)

with EdhConnector(config) as edh:
    # 1. Store raw document bytes in QUARANTINE or RAW zone with SHA-256 & idempotency
    meta = edh.put_bytes(
        "kb-legal/contracts/msa_2026.pdf",
        pdf_bytes,
        zone=DataZone.RAW,
        options=EdhWriteOptions(
            content_type="application/pdf",
            custom_metadata={"tenant_id": "acme", "classification": "confidential"},
            idempotency_key="ingest-req-001",
            overwrite=False,
        ),
    )

    # 2. Stream chunks back without loading large blobs into memory
    for chunk in edh.stream_chunks(meta.ref, chunk_size=65536):
        process_chunk(chunk)

    # 3. Write structured, Hive-partitioned records to the CURATED zone
    batch = EdhRecordBatch(
        dataset="rag_chunks",
        records=(
            {"chunk_id": "c-01", "doc_id": "msa_2026", "text": "Clause 1..."},
            {"chunk_id": "c-02", "doc_id": "msa_2026", "text": "Clause 2..."},
        ),
        partition_values={"tenant_id": "acme", "kb_id": "kb-legal"},
        zone=DataZone.CURATED,
        record_format=RecordFormat.JSONL,
    )
    edh.write_records(batch, options=EdhWriteOptions(compress_gzip=True))

    # 4. Read records with partition pruning
    for record in edh.read_records(
        "rag_chunks",
        zone=DataZone.CURATED,
        partition_filter={"tenant_id": "acme"},
    ):
        index_record(record)
```

### 4.2 Connecting to Salesforce via EDH (`strategy="salesforce"`)
```python
from sdk.connectors.edh import (
    DataZone,
    EdhConnector,
    EdhConnectorConfig,
    EdhRecordBatch,
    make_salesforce_oauth_token_getter,
)

sf_token_getter = make_salesforce_oauth_token_getter(
    instance_url="https://your-org.my.salesforce.com",
    client_id_getter=lambda: settings.SF_CLIENT_ID,
    client_secret_getter=lambda: settings.SF_CLIENT_SECRET.get_secret_value(),
)

sf_config = EdhConnectorConfig(
    strategy="salesforce",  # aliases: "sfdc", "salesforce_crm"
    namespace="crm-edh",
    endpoint_url="https://your-org.my.salesforce.com",
    auth_token_getter=sf_token_getter,
)

with EdhConnector(sf_config) as sf:
    # 1. Read Salesforce Knowledge articles or custom SObjects via SOQL
    for article in sf.read_records(
        "Knowledge__kav",
        zone=DataZone.CURATED,
        partition_filter={"PublishStatus": "Online", "Language": "en_US"},
    ):
        index_article(article)

    # 2. Download or upload Salesforce Files (ContentVersion)
    raw_pdf = sf.get_bytes("contracts/2026/renewal.pdf", zone=DataZone.RAW)
```

### 4.3 Wiring into FastAPI Readiness Probes
`EdhConnector.ping()` checks both the circuit breaker state and the underlying strategy health probe, making it a drop-in readiness check for `create_base_app`:

```python
from fastapi.concurrency import run_in_threadpool
from sdk import create_base_app

async def edh_ready() -> bool:
    return await run_in_threadpool(edh_connector.ping)

app = create_base_app(
    title="RAG Ingestion Service",
    readiness_check=edh_ready,
)
```
