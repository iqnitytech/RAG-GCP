import codecs
import logging
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.core.security import require_api_key
from app.dependencies import get_storage_service_dependency
from app.services.storage.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
    dependencies=[Depends(require_api_key)],
)

# Content type is derived from the validated extension, never from the client.
ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
# Multipart framing adds a little on top of the file itself.
MAX_REQUEST_SIZE = MAX_FILE_SIZE + 1024 * 1024
HEAD_BYTES = 4096


def _safe_filename(name: str) -> str:
    """Return a storage-safe filename (ASCII, no path chars, bounded length)."""
    path = Path(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "document"
    return f"{stem[:100]}{path.suffix.lower()}"


def _content_matches_extension(extension: str, head: bytes) -> bool:
    """Check file magic bytes so a renamed file cannot masquerade as a document."""
    if extension == ".pdf":
        return head.startswith(b"%PDF-")
    if extension == ".docx":
        return head.startswith(b"PK\x03\x04")
    if extension == ".txt":
        if b"\x00" in head:
            return False
        try:
            codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        except UnicodeDecodeError:
            return False
        return True
    return False


@router.post("/upload", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    storage_service: StorageService = Depends(get_storage_service_dependency),
):
    """Validate an uploaded document and store it in GCS.

    The endpoint is async; the blocking GCS upload runs in a worker thread so
    the event loop is never blocked. Storage failures bubble up to the global
    StorageServiceError handler (503) without leaking internal details.
    """
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > MAX_REQUEST_SIZE:
        raise HTTPException(status_code=413, detail="Maximum file size is 10 MB")

    try:
        if file.filename is None or not file.filename.strip():
            raise HTTPException(status_code=400, detail="Filename is required")

        original_filename = Path(file.filename).name
        extension = Path(original_filename).suffix.lower()
        if extension not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail="Only PDF, TXT and DOCX files are supported",
            )

        # Size: UploadFile.size is exact once the body has been parsed.
        file_size = file.size
        if file_size is None:
            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)
        if file_size == 0:
            raise HTTPException(status_code=400, detail="Empty files are not allowed")
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="Maximum file size is 10 MB")

        head = await file.read(HEAD_BYTES)
        await file.seek(0)
        if not _content_matches_extension(extension, head):
            raise HTTPException(
                status_code=400,
                detail="File content does not match its extension",
            )

        safe_name = _safe_filename(original_filename)
        destination_name = f"documents/{uuid4().hex}_{safe_name}"

        result = await run_in_threadpool(
            storage_service.upload_file,
            file_object=file.file,
            destination_name=destination_name,
            content_type=ALLOWED_TYPES[extension],
            metadata={"original_filename": original_filename[:255]},
        )

        logger.info("Document uploaded -- object=%s size=%d", result.object_name, file_size)
        return {
            "message": "Document uploaded successfully",
            "filename": original_filename,
            "size_bytes": file_size,
            "storage": result.to_dict(),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
