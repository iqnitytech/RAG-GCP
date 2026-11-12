"""Salesforce SObject and ContentVersion constants for Backend integration tests.

Modify ``SalesforceObjects`` below to target specific standard or custom
Salesforce SObjects, filters, and file prefixes in your Salesforce org.
"""

from __future__ import annotations


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

    # Active SObject and filter used by test_salesforce_edh_integration.py
    TARGET_SOBJECT: str = ACCOUNT
    TARGET_SOBJECT_FILTER: dict[str, str] = {}

    # ContentVersion file listing and upload probe settings
    CONTENT_VERSION_PREFIX: str = ""
    UPLOAD_OBJECT_KEY: str = "policies/security.pdf"
    UPLOAD_BYTES: bytes = b"%PDF-1.7 Salesforce EDH integration test document"


__all__ = ["SalesforceObjects"]
