"""Real object names, keys, SObject/table identifiers, and filters for Backend integration tests.

Edit the constants below alongside ``backend/tests/integration/.env.integration``
to point integration tests at your real Salesforce SObjects, Databricks Unity Catalog
tables/volumes, and document upload payloads.
"""

from __future__ import annotations

DEFAULT_PAGE_SIZE = 10

# ---------------------------------------------------------------------------
# 1. Document Upload -> EDH Data Lake Constants
# ---------------------------------------------------------------------------
UPLOAD_TEST_FILENAME = "Architecture Spec 2026.pdf"
UPLOAD_TEST_CONTENT_TYPE = "application/pdf"
UPLOAD_TEST_PDF_BYTES = b"%PDF-1.7\nEnterprise architecture specification document."
UPLOAD_LIST_PREFIX = "documents/"
UPLOAD_REQUEST_ID = "int-req-101"

# ---------------------------------------------------------------------------
# 2. Salesforce Integration Constants (strategy="salesforce")
# ---------------------------------------------------------------------------
SALESFORCE_SOBJECT_NAME = "Account"
SALESFORCE_SOBJECT_FILTER: dict[str, str] = {}
SALESFORCE_CONTENT_VERSION_PREFIX = ""
SALESFORCE_UPLOAD_OBJECT_KEY = "policies/security.pdf"
SALESFORCE_UPLOAD_BYTES = b"%PDF-1.7 Salesforce EDH integration test document"

# ---------------------------------------------------------------------------
# 3. Databricks Integration Constants (strategy="databricks")
# ---------------------------------------------------------------------------
DATABRICKS_TABLE_NAME = "rag_chunks"
DATABRICKS_TABLE_FILTER: dict[str, str] = {}
DATABRICKS_VOLUME_PREFIX = ""
DATABRICKS_VOLUME_OBJECT_KEY = "docs/guide.pdf"
DATABRICKS_VOLUME_BYTES = b"%PDF-1.7 Databricks Unity Catalog Volume integration test document"
