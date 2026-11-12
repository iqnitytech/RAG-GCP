"""Real object names, keys, SObject/table identifiers, and filters for SDK integration tests.

Edit the constants below alongside ``rag-platform-sdk/tests/integration/.env.integration``
to point integration tests at your real Salesforce SObjects, Databricks Unity Catalog
tables/volumes, Object Storage buckets, and API Hub datasets.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# General Integration Defaults
# ---------------------------------------------------------------------------
DEFAULT_NAMESPACE = "crm-edh"
DEFAULT_PAGE_SIZE = 10

# ---------------------------------------------------------------------------
# 1. Salesforce Integration Constants (strategy="salesforce")
# ---------------------------------------------------------------------------
# Standard or custom SObject to query in read_records (e.g. "Account", "Contact", "Case", "Knowledge__kav")
SALESFORCE_SOBJECT_NAME = "Account"
# Optional WHERE filter dictionary for read_records (leave empty {} to fetch up to LIMIT 200 records)
SALESFORCE_SOBJECT_FILTER: dict[str, str] = {}
# Prefix for listing Salesforce ContentVersion files in list_objects
SALESFORCE_CONTENT_VERSION_PREFIX = ""
# Optional key & payload if testing ContentVersion upload/download
SALESFORCE_UPLOAD_OBJECT_KEY = "documents/sf_integration_probe.txt"
SALESFORCE_UPLOAD_BYTES = b"Salesforce EDH integration test document payload"

# ---------------------------------------------------------------------------
# 2. Databricks Integration Constants (strategy="databricks")
# ---------------------------------------------------------------------------
# Unity Catalog table name (under DATABRICKS_CATALOG.DATABRICKS_SCHEMA) for read_records
DATABRICKS_TABLE_NAME = "rag_chunks"
# Optional WHERE filter dictionary for read_records
DATABRICKS_TABLE_FILTER: dict[str, str] = {}
# Unity Catalog Volume prefix for list_objects
DATABRICKS_VOLUME_PREFIX = ""
# Optional Volume file key & payload for put_bytes / get_bytes
DATABRICKS_VOLUME_OBJECT_KEY = "documents/dbx_integration_probe.txt"
DATABRICKS_VOLUME_BYTES = b"Databricks Unity Catalog Volume integration test payload"

# ---------------------------------------------------------------------------
# 3. Object Storage Hub Integration Constants (strategy="object_storage_hub")
# ---------------------------------------------------------------------------
OBJECT_STORAGE_PREFIX = "documents"
OBJECT_STORAGE_OBJECT_KEY = "documents/s3_integration_probe.txt"
OBJECT_STORAGE_BYTES = b"Object Storage Hub integration test payload"
OBJECT_STORAGE_CUSTOM_METADATA = {"department": "engineering"}

# ---------------------------------------------------------------------------
# 4. API-Driven Hub Integration Constants (strategy="api_hub")
# ---------------------------------------------------------------------------
API_HUB_PREFIX = "documents"
API_HUB_OBJECT_KEY = "documents/api_hub_integration_probe.pdf"
API_HUB_BYTES = b"%PDF-1.4 API Hub integration test payload"
API_HUB_DATASET = "chunks/legal"
API_HUB_PARTITION_FILTER = {"tenant": "acme"}

# ---------------------------------------------------------------------------
# 5. Data Lake Integration Constants (strategy="data_lake")
# ---------------------------------------------------------------------------
DATA_LAKE_QUARANTINE_KEY = "uploads/inv_9001.pdf"
DATA_LAKE_RAW_KEY = "invoices/2026/inv_9001.pdf"
DATA_LAKE_SAMPLE_PDF_BYTES = b"%PDF-1.4 scanned invoice #9001"
DATA_LAKE_DATASET = "invoice_chunks"
DATA_LAKE_PARTITION_VALUES = {"year": "2026", "status": "verified"}
DATA_LAKE_RECORDS = (
    {"chunk_id": "inv-1", "amount": "1500.00", "currency": "USD"},
    {"chunk_id": "inv-2", "amount": "250.00", "currency": "USD"},
)
