"""Response models for Salesforce API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SalesforceRecordResponse(BaseModel):
    """Response payload returned by ``GET /salesforce/{sobject_name}/records/{record_id}``."""

    sobject: str = Field(..., description="Salesforce SObject API name (e.g. Account, Contact)")
    record_id: str = Field(..., description="Salesforce 15- or 18-character Record ID")
    record: dict[str, Any] = Field(..., description="Salesforce SObject record fields")


__all__ = ["SalesforceRecordResponse"]
