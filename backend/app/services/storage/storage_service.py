import logging
from functools import lru_cache
from io import IOBase
from typing import BinaryIO

from google.cloud import storage
from sdk.errors import ServiceUnavailableError

from app.auth.gcp import get_gcp_credentials
from app.core.config import Settings, get_settings
from app.models.service_models import UploadResult

logger = logging.getLogger(__name__)

# Seconds. Bounded so a stuck GCS call can never hang a worker forever.
UPLOAD_TIMEOUT_SECONDS = 120
PING_TIMEOUT_SECONDS = 5

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class StorageServiceError(ServiceUnavailableError):
    """Raised when a GCS operation fails (clients see a generic 503)."""

    public_message = "Storage service is temporarily unavailable"

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class StorageService:
    """Thin wrapper around the GCS client scoped to the configured bucket."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialise the GCS client and resolve the target bucket.

        Args:
            settings: App settings used to resolve the configured project and bucket.

        Raises:
            StorageServiceError: If the GCS client or bucket cannot be created.
        """
        resolved_settings = settings or get_settings()

        if not resolved_settings.gcp_project_id:
            raise StorageServiceError("GCP project ID is not configured.")
        if not resolved_settings.gcs_document_bucket:
            raise StorageServiceError("GCS document bucket is not configured.")

        try:
            credentials = get_gcp_credentials()
            self._client = storage.Client(
                project=resolved_settings.gcp_project_id,
                credentials=credentials,
            )
            self._bucket = self._client.bucket(resolved_settings.gcs_document_bucket)
            logger.info(
                "StorageService initialised -- bucket=%s project=%s auth_mode=%s",
                resolved_settings.gcs_document_bucket,
                resolved_settings.gcp_project_id,
                resolved_settings.gcp_auth_mode,
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
        metadata: dict[str, str] | None = None,
    ) -> UploadResult:
        """Upload a binary file-like object to GCS.

        Args:
            file_object: An open, readable binary stream (e.g. SpooledTemporaryFile).
            destination_name: The object key (path) inside the bucket.
                Must be a non-empty string.
            content_type: MIME type of the file (e.g. ``"application/pdf"``).
            metadata: Optional custom object metadata (e.g. original filename).

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
            if metadata:
                blob.metadata = metadata
            # if_generation_match=0 -> never overwrite an existing object, and it
            # makes the upload safe for the client library to retry on transient
            # errors. The timeout bounds every attempt.
            blob.upload_from_file(
                file_object,
                rewind=True,
                content_type=resolved_content_type,
                if_generation_match=0,
                timeout=UPLOAD_TIMEOUT_SECONDS,
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


    def ping(self) -> bool:
        """Return True if the configured bucket is reachable (readiness probe)."""
        try:
            return bool(self._bucket.exists(timeout=PING_TIMEOUT_SECONDS))
        except Exception:
            logger.exception("Bucket readiness check failed")
            return False

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._client.close()


# ---------------------------------------------------------------------------
# Dependency-injection factory (preferred over a module-level singleton)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    """Return a cached StorageService instance.

    The service is created lazily on first use and then reused for the
    lifetime of the process. This keeps the expensive GCS client creation
    in one place while allowing callers to inject explicit settings when
    needed for tests or dependency wiring.

    Returns:
        A singleton StorageService.
    """
    return StorageService(get_settings())


