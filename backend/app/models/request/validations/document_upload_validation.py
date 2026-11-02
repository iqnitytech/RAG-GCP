"""Validation rules for the document upload request.

All checks for ``POST /documents/upload`` live here so the route handler only
orchestrates. Every failure raises ``HTTPException`` with a client-safe message.
"""

import codecs
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import HTTPException

if TYPE_CHECKING:  # avoid a runtime import cycle with the request model
    from app.models.request.document_upload import DocumentUploadRequest

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
MAX_FILENAME_LENGTH = 100


@dataclass(frozen=True)
class ValidatedDocument:
    """Result of a successful validation, ready to hand to the storage layer."""

    original_filename: str
    extension: str
    size_bytes: int
    content_type: str
    destination_name: str


def validate_content_length(content_length: int | None) -> None:
    """Reject missing or oversized declared request bodies before reading them."""
    if content_length is None:
        raise HTTPException(status_code=411, detail="Content-Length header is required")
    if content_length > MAX_REQUEST_SIZE:
        raise HTTPException(status_code=413, detail="Maximum file size is 10 MB")


def validate_filename(filename: str | None) -> tuple[str, str]:
    """Return ``(original_filename, extension)`` or raise 400."""
    if filename is None or not filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required")

    original_filename = Path(filename).name
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, TXT and DOCX files are supported",
        )
    return original_filename, extension


def validate_size(size_bytes: int) -> None:
    """Reject empty and oversized files."""
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="Empty files are not allowed")
    if size_bytes > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Maximum file size is 10 MB")


def validate_content_matches_extension(extension: str, head: bytes) -> None:
    """Check magic bytes so a renamed file cannot masquerade as a document."""
    if extension == ".pdf":
        ok = head.startswith(b"%PDF-")
    elif extension == ".docx":
        ok = head.startswith(b"PK\x03\x04")
    elif extension == ".txt":
        ok = b"\x00" not in head
        if ok:
            try:
                codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
            except UnicodeDecodeError:
                ok = False
    else:
        ok = False

    if not ok:
        raise HTTPException(
            status_code=400,
            detail="File content does not match its extension",
        )


def safe_filename(name: str) -> str:
    """Return a storage-safe filename (ASCII, no path characters, bounded length)."""
    path = Path(name)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "document"
    return f"{stem[:MAX_FILENAME_LENGTH]}{path.suffix.lower()}"


async def validate_document_upload(request: "DocumentUploadRequest") -> ValidatedDocument:
    """Run every upload check in order and return the validated description."""
    validate_content_length(request.content_length)
    original_filename, extension = validate_filename(request.file.filename)

    # UploadFile.size is exact once the body has been parsed.
    size_bytes = request.file.size
    if size_bytes is None:
        request.file.file.seek(0, 2)
        size_bytes = request.file.file.tell()
        request.file.file.seek(0)
    validate_size(size_bytes)

    head = await request.file.read(HEAD_BYTES)
    await request.file.seek(0)
    validate_content_matches_extension(extension, head)

    return ValidatedDocument(
        original_filename=original_filename,
        extension=extension,
        size_bytes=size_bytes,
        content_type=ALLOWED_TYPES[extension],
        destination_name=f"documents/{uuid4().hex}_{safe_filename(original_filename)}",
    )

