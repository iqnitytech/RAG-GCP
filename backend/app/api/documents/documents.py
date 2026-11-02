import logging

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.core.rate_limit import rate_limit
from app.core.security import require_api_key
from app.dependencies import get_storage_service_dependency
from app.models.request.document_upload import (
    DocumentUploadRequest,
    get_document_upload_request,
)
from app.models.response.document_upload import DocumentUploadResponse, StorageInfo
from app.services.storage.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)


@router.post("/upload", status_code=201, response_model=DocumentUploadResponse)
async def upload_document(
    upload: DocumentUploadRequest = Depends(get_document_upload_request),
    storage_service: StorageService = Depends(get_storage_service_dependency),
) -> DocumentUploadResponse:
    """Validate an uploaded document and store it in GCS.

    Validation lives in ``app.models.request.validations``. The blocking GCS
    upload runs in a worker thread so the event loop is never blocked; storage
    failures are mapped to a generic 503 by the global handler.
    """
    try:
        document = await upload.validate_request()

        result = await run_in_threadpool(
            storage_service.upload_file,
            file_object=upload.file.file,
            destination_name=document.destination_name,
            content_type=document.content_type,
            metadata={"original_filename": document.original_filename[:255]},
        )

        logger.info(
            "Document uploaded -- object=%s size=%d",
            result.object_name,
            document.size_bytes,
        )
        return DocumentUploadResponse(
            message="Document uploaded successfully",
            filename=document.original_filename,
            size_bytes=document.size_bytes,
            storage=StorageInfo(**result.to_dict()),
        )
    finally:
        await upload.file.close()
