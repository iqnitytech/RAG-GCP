from app.core.config import Settings, get_settings
from app.services.ingestion.factory import get_ingestion_service
from app.services.ingestion.service import IngestionService
from app.services.salesforce.salesforce_service import (
    SalesforceService,
    get_salesforce_service,
)
from app.services.storage.storage_service import StorageService, get_storage_service


def get_app_settings() -> Settings:
    """Return the cached application settings object."""
    return get_settings()


def get_storage_service_dependency() -> StorageService:
    """FastAPI dependency that resolves a production-safe storage service."""
    return get_storage_service()


def get_salesforce_service_dependency() -> SalesforceService:
    """FastAPI dependency that resolves the Salesforce EDH connector service."""
    return get_salesforce_service()


def get_ingestion_service_dependency() -> IngestionService:
    """FastAPI dependency that resolves the document ingestion service."""
    return get_ingestion_service()
