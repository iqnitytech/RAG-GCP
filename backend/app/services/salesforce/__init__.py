"""Salesforce service package."""

from app.services.salesforce.salesforce_service import (
    SalesforceService,
    get_salesforce_service,
)

__all__ = ["SalesforceService", "get_salesforce_service"]
