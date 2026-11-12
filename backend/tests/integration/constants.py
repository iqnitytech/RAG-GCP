"""Generic integration test constants for Backend integration tests.

Salesforce-specific SObject definitions live in
``backend/tests/integration/salesforce_objects.py`` (``SalesforceObjects``).
"""

from __future__ import annotations

DEFAULT_PAGE_SIZE = 10

# ---------------------------------------------------------------------------
# Document Upload -> EDH Data Lake Constants
# ---------------------------------------------------------------------------
UPLOAD_TEST_FILENAME = "Architecture Spec 2026.pdf"
UPLOAD_TEST_CONTENT_TYPE = "application/pdf"
UPLOAD_TEST_PDF_BYTES = b"%PDF-1.7\nEnterprise architecture specification document."
UPLOAD_LIST_PREFIX = "documents/"
UPLOAD_REQUEST_ID = "int-req-101"

# ---------------------------------------------------------------------------
# Databricks Integration Constants (strategy="databricks")
# ---------------------------------------------------------------------------
DATABRICKS_TABLE_NAME = "rag_chunks"
DATABRICKS_TABLE_FILTER: dict[str, str] = {}
DATABRICKS_VOLUME_PREFIX = ""
DATABRICKS_VOLUME_OBJECT_KEY = "docs/guide.pdf"
DATABRICKS_VOLUME_BYTES = b"%PDF-1.7 Databricks Unity Catalog Volume integration test document"
