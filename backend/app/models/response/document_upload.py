"""Response model for ``POST /documents/upload``."""

from pydantic import BaseModel


class StorageInfo(BaseModel):
    bucket: str
    object_name: str
    gs_url: str


class DocumentUploadResponse(BaseModel):
    message: str
    filename: str
    size_bytes: int
    storage: StorageInfo

