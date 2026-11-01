from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.services.storage.storage_service import StorageService, get_storage_service


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/upload", status_code=201)
def upload_document(
    file: UploadFile = File(...),
    storage_service: StorageService = Depends(get_storage_service),
):
    if file.filename is None or not file.filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required")

    original_filename = Path(file.filename).name
    extension = Path(original_filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, TXT and DOCX files are supported",
        )

    try:
        if not hasattr(file.file, "seek") or not hasattr(file.file, "tell"):
            raise HTTPException(status_code=400, detail="Uploaded file stream is invalid")

        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        if file_size == 0:
            raise HTTPException(status_code=400, detail="Empty files are not allowed")
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="Maximum file size is 10 MB")

        unique_filename = f"{uuid4().hex}_{original_filename}"
        destination_name = f"documents/{unique_filename}"

        result = storage_service.upload_file(
            file_object=file.file,
            destination_name=destination_name,
            content_type=file.content_type or "application/octet-stream",
        )

        return {
            "message": "Document uploaded successfully",
            "filename": original_filename,
            "size_bytes": file_size,
            "storage": result.to_dict(),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Document upload failed: {exc}",
        ) from exc
    finally:
        try:
            file.file.close()
        except Exception:
            pass

