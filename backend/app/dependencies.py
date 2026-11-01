from app.core.config import Settings, get_settings
from app.services.storage.storage_service import StorageService, get_storage_service


def get_app_settings() -> Settings:
    """Return the cached application settings object."""
    return get_settings()


def get_storage_service_dependency() -> StorageService:
    """FastAPI dependency that resolves a production-safe storage service."""
    return get_storage_service()
