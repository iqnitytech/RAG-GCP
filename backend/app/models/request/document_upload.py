"""Request model for ``POST /documents/upload``."""

from fastapi import Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.security import rate_limit, require_api_key
from app.models.request.validations.document_upload_validation import (
    ValidatedDocument,
    validate_content_length,
    validate_document_upload,
)


class DocumentUploadRequest(BaseModel):
    """Everything the upload route needs from the incoming HTTP request."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    file: StarletteUploadFile
    content_length: int | None = None

    async def validate_request(self) -> ValidatedDocument:
        """Run the upload validations (see ``document_upload_validation``)."""
        return await validate_document_upload(self)


async def get_document_upload_request(
    request: Request,
    _auth: None = Depends(require_api_key),
    _rate_limit: None = Depends(rate_limit),
) -> DocumentUploadRequest:
    """FastAPI dependency that builds the request model AFTER auth & rate-limit.

    Parsing the multipart form here (instead of a ``File(...)`` parameter)
    ensures unauthenticated, rate-limited, or oversized requests are rejected
    before reading or spooling the multipart body.
    """
    declared = request.headers.get("content-length")
    content_length = int(declared) if declared and declared.isdigit() else None
    validate_content_length(content_length)

    form = await request.form()
    raw_file = form.get("file")
    if not isinstance(raw_file, (UploadFile, StarletteUploadFile)):
        raise HTTPException(status_code=422, detail="Field 'file' is required")

    return DocumentUploadRequest(
        file=raw_file,
        content_length=content_length,
    )



