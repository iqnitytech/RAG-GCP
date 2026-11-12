"""Salesforce API router implementing ``getRecordById`` via the RAG Platform SDK."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.core.security import rate_limit, require_api_key
from app.dependencies import get_salesforce_service_dependency
from app.models.response.salesforce import SalesforceRecordResponse
from app.services.salesforce.salesforce_service import SalesforceService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/salesforce",
    tags=["Salesforce"],
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)


@router.get(
    "/{sobject_name}/records/{record_id}",
    response_model=SalesforceRecordResponse,
    summary="Get Salesforce SObject record by ID",
)
@router.get(
    "/records/{sobject_name}/{record_id}",
    response_model=SalesforceRecordResponse,
    include_in_schema=False,
)
async def get_salesforce_record_by_id(
    sobject_name: str,
    record_id: str,
    fields: str | None = Query(
        default=None,
        description="Optional comma-separated Salesforce field names (e.g. Id,Name,Industry)",
    ),
    salesforce_service: SalesforceService = Depends(get_salesforce_service_dependency),
) -> SalesforceRecordResponse:
    """Connect to Salesforce via the SDK ``AsyncEdhConnector`` and fetch a record by ID.

    Automatically opens and closes the underlying Salesforce connection pool and
    maps any Salesforce REST or validation errors to structured ``SalesforceErrorConstants``
    codes and HTTP status codes.
    """
    parsed_fields = (
        [f.strip() for f in fields.split(",") if f.strip()]
        if fields
        else None
    )
    record = await salesforce_service.get_record_by_id(
        sobject_name,
        record_id,
        fields=parsed_fields,
    )
    return SalesforceRecordResponse(
        sobject=sobject_name,
        record_id=record_id,
        record=record,
    )
