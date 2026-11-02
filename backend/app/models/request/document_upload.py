"""Request model for ``POST /documents/upload``."""

from fastapi import File, Request, UploadFile
from pydantic import BaseModel, ConfigDict

from app.models.request.validations.document_upload_validation import (
    ValidatedDocument,
    validate_document_upload,
)


class DocumentUploadRequest(BaseModel):
    """Everything the upload route needs from the incoming HTTP request."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    file: UploadFile
    content_length: int | None = None

    async def validate_request(self) -> ValidatedDocument:
        """Run the upload validations (see ``document_upload_validation``)."""
        return await validate_document_upload(self)


async def get_document_upload_request(
    request: Request,
    file: UploadFile = File(...),
) -> DocumentUploadRequest:
    """FastAPI dependency that builds the request model from the raw request."""
    declared = request.headers.get("content-length")
    return DocumentUploadRequest(
        file=file,
        content_length=int(declared) if declared and declared.isdigit() else None,
    )

