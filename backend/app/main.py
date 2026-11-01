from fastapi import FastAPI

from app.api.documents.documents import router as documents_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    if not settings.gcp_project_id:
        raise RuntimeError("GCP project ID is not configured.")
    if not settings.gcs_document_bucket:
        raise RuntimeError("GCS document bucket is not configured.")

    app = FastAPI(
        title=settings.app_name,
        description="Enterprise Retrieval Augmented Generation API.",
        version="1.0.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    @app.get("/")
    async def read_root():
        if settings.is_production:
            return {"message": "Enterprise RAG API is running!"}
        return {
            "message": "Enterprise RAG API is running!",
            "environment": settings.app_env,
            "project_id": settings.gcp_project_id,
            "region": settings.gcp_region,
        }

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    app.include_router(documents_router)
    return app


app = create_app()
