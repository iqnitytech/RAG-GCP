import logging
from dataclasses import dataclass
from functools import lru_cache
from io import IOBase
from typing import BinaryIO
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError

from app.core.config import settings
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class StorageServiceError(RuntimeError):
    """Raised when a GCS operation fails."""

# ---------------------------------------------------------------------------
# Typed return value
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UploadResult:
    """Immutable result returned after a successful GCS upload."""

    bucket: str
    object_name: str
    gs_url: str

    def to_dict(self) -> dict[str, str]:
        """Serialise to a plain dict (e.g. for JSON responses)."""
        return {
            "bucket": self.bucket,
            "object_name": self.object_name,
            "gs_url": self.gs_url,
        }

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class StorageService:
    """Thin wrapper around the GCS client scoped to the configured bucket."""

    def __init__(self) -> None:
        """Initialise the GCS client and resolve the target bucket.

        Raises:
            StorageServiceError: If the GCS client or bucket cannot be created.
        """
        if not settings.gcp_project_id:
            raise StorageServiceError("GCP project ID is not configured.")
        if not settings.gcs_document_bucket:
            raise StorageServiceError("GCS document bucket is not configured.")

        try:
            self._client = storage.Client(project=settings.gcp_project_id)
            self._bucket = self._client.bucket(settings.gcs_document_bucket)
            logger.info(
                "StorageService initialised -- bucket=%s project=%s",
                settings.gcs_document_bucket,
                settings.gcp_project_id,
            )
        except Exception as exc:  # pragma: no cover - defensive guard around GCP client init
            raise StorageServiceError(
                f"Failed to initialise GCS client: {exc}"
            ) from exc

    @staticmethod
    def _normalise_destination_name(destination_name: str) -> str:
        """Validate and normalise object paths used as GCS keys."""
        if not isinstance(destination_name, str):
            raise ValueError("destination_name must be a string.")

        cleaned = destination_name.strip()
        if not cleaned:
            raise ValueError("destination_name must be a non-empty string.")

        if cleaned.startswith("/"):
            cleaned = cleaned.lstrip("/")
        if cleaned.startswith("../") or "//" in cleaned:
            raise ValueError("destination_name contains an invalid path.")
    return cleaned

    @staticmethod
    def _validate_file_object(file_object: BinaryIO) -> None:
        """Check that the upload stream is readable and consistent with GCS usage."""
        if not hasattr(file_object, "read"):
            raise ValueError("file_object must be an open binary stream with a read() method.")
        if not hasattr(file_object, "readable") or not file_object.readable():
            raise ValueError("file_object must be an open, readable binary stream.")
        if not isinstance(file_object, IOBase):
            raise ValueError("file_object must be a file-like object backed by IOBase.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_file(
        self,
        file_object: BinaryIO,
        destination_name: str,
        content_type: str,
    ) -> UploadResult:
        """Upload a binary file-like object to GCS.

        Args:
            file_object: An open, readable binary stream (e.g. SpooledTemporaryFile).
            destination_name: The object key (path) inside the bucket.
                Must be a non-empty string.
            content_type: MIME type of the file (e.g. ``"application/pdf"``).

        Returns:
            An UploadResult containing the bucket name, object key,
            and the canonical ``gs://`` URL.

        Raises:
            ValueError: If *destination_name* is empty or *file_object* is not readable.
            StorageServiceError: If the GCS upload fails.
        """
        normalised_destination = self._normalise_destination_name(destination_name)
        self._validate_file_object(file_object)

        resolved_content_type = (content_type or "application/octet-stream").strip()
        if not resolved_content_type:
            resolved_content_type = "application/octet-stream"

        logger.debug(
            "Uploading object -- destination=%s content_type=%s bucket=%s",
            normalised_destination,
            resolved_content_type,
            self._bucket.name,
        )

        try:
            blob = self._bucket.blob(normalised_destination)
            blob.upload_from_file(
                file_object,
                rewind=True,
                content_type=resolved_content_type,
            )
        except Exception as exc:
            logger.exception(
                "GCS upload failed -- destination=%s bucket=%s",
                normalised_destination,
                self._bucket.name,
            )
            raise StorageServiceError(
                f"Upload of `{normalised_destination}` to bucket "
                f"`{self._bucket.name}` failed: {exc}"
            ) from exc

        result = UploadResult(
            bucket=self._bucket.name,
            object_name=normalised_destination,
            gs_url=f"gs://{self._bucket.name}/{normalised_destination}",
        )
        logger.info("Upload successful -- gs_url=%s", result.gs_url)
    return result


# ---------------------------------------------------------------------------
# Dependency-injection factory (preferred over a module-level singleton)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    """Return a cached StorageService instance.

    The service is created lazily on first use and then reused for the
    lifetime of the process. FastAPI can inject this via ``Depends()`` and
    tests can override it cleanly.

    Returns:
        A singleton StorageService.
    """
return StorageService()


