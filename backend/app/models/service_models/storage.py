"""Service-layer value objects and return models."""

from dataclasses import dataclass


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
