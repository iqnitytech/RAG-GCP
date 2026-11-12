"""Generic integration test constants for SDK EDH strategies.

Salesforce-specific SObject definitions live in
``rag-platform-sdk/tests/integration/salesforce_objects.py`` (``SalesforceObjects``).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# General Integration Defaults
# ---------------------------------------------------------------------------
DEFAULT_NAMESPACE = "crm-edh"
DEFAULT_PAGE_SIZE = 10

# ---------------------------------------------------------------------------
# Databricks Integration Constants (strategy="databricks")
# ---------------------------------------------------------------------------
DATABRICKS_TABLE_NAME = "rag_chunks"
DATABRICKS_TABLE_FILTER: dict[str, str] = {}
DATABRICKS_VOLUME_PREFIX = ""
DATABRICKS_VOLUME_OBJECT_KEY = "documents/dbx_integration_probe.txt"
DATABRICKS_VOLUME_BYTES = b"Databricks Unity Catalog Volume integration test payload"

# ---------------------------------------------------------------------------
# Object Storage Hub Integration Constants (strategy="object_storage_hub")
# ---------------------------------------------------------------------------
OBJECT_STORAGE_PREFIX = "documents"
OBJECT_STORAGE_OBJECT_KEY = "documents/s3_integration_probe.txt"
OBJECT_STORAGE_BYTES = b"Object Storage Hub integration test payload"
OBJECT_STORAGE_CUSTOM_METADATA = {"department": "engineering"}

# ---------------------------------------------------------------------------
# API-Driven Hub Integration Constants (strategy="api_hub")
# ---------------------------------------------------------------------------
API_HUB_PREFIX = "documents"
API_HUB_OBJECT_KEY = "documents/api_hub_integration_probe.pdf"
API_HUB_BYTES = b"%PDF-1.4 API Hub integration test payload"
API_HUB_DATASET = "chunks/legal"
API_HUB_PARTITION_FILTER = {"tenant": "acme"}

# ---------------------------------------------------------------------------
# Data Lake Integration Constants (strategy="data_lake")
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
