"""Salesforce SObject and ContentVersion constants for SDK integration tests.

Modify ``SalesforceObjects`` below to target specific standard or custom
Salesforce SObjects, filters, fields, and payloads in your Salesforce org.
"""

from __future__ import annotations

from typing import Any


class SalesforceObjects:
    """Standard and custom Salesforce object definitions for integration tests."""

    # Standard CRM SObjects
    ACCOUNT = "Account"
    CONTACT = "Contact"
    CASE = "Case"
    OPPORTUNITY = "Opportunity"
    LEAD = "Lead"

    # Knowledge & Files SObjects
    KNOWLEDGE_ARTICLE = "Knowledge__kav"
    CONTENT_VERSION = "ContentVersion"

    # Active SObject and filter used by test_salesforce_integration.py
    TARGET_SOBJECT: str = ACCOUNT
    TARGET_SOBJECT_FILTER: dict[str, str] = {}

    # 2. Query & 3. PaginatedQuery & 9. BulkRead settings
    SELECT_FIELDS: tuple[str, ...] = ("Id", "Name")
    QUERY_LIMIT: int = 10
    PAGINATED_BATCH_SIZE: int = 200

    # 5. Create & 6. Update payloads
    CREATE_PAYLOAD: dict[str, Any] = {"Name": "EDH Integration Test Account"}
    UPDATE_PAYLOAD: dict[str, Any] = {"Name": "EDH Integration Test Account (Updated)"}

    # 7. Upsert settings (populate UPSERT_EXTERNAL_ID_FIELD if testing Upsert against a custom External ID field)
    UPSERT_EXTERNAL_ID_FIELD: str = ""
    UPSERT_EXTERNAL_ID_VALUE: str = "EDH-EXT-001"
    UPSERT_PAYLOAD: dict[str, Any] = {"Name": "EDH Upsert Integration Test Account"}

    # 10. BulkWrite records
    BULK_WRITE_RECORDS: tuple[dict[str, Any], ...] = (
        {"Name": "EDH Bulk Integration Account 1"},
        {"Name": "EDH Bulk Integration Account 2"},
    )

    # ContentVersion file listing and upload probe settings
    CONTENT_VERSION_PREFIX: str = ""
    UPLOAD_OBJECT_KEY: str = "documents/sf_integration_probe.txt"
    UPLOAD_BYTES: bytes = b"Salesforce EDH integration test document payload"


__all__ = ["SalesforceObjects"]
