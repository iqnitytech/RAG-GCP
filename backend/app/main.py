import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.documents.documents import router as documents_router
from app.core.config import get_settings
from app.services.storage.storage_service import (
    StorageServiceError,
    get_storage_service,
)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    if not settings.gcp_project_id:
        raise RuntimeError("GCP project ID is not configured.")
    if not settings.gcs_document_bucket:
        raise RuntimeError("GCS document bucket is not configured.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Warm up the GCS client (credential resolution is blocking, so it
        # runs in a worker thread). Production fails fast; development keeps
        # starting so you can fix credentials without a crash loop.
        try:
            await run_in_threadpool(get_storage_service)
            logger.info("Storage service ready")
        except Exception:
            if settings.is_production:
                raise
            logger.exception(
                "Storage service failed to initialise (continuing in %s)",
                settings.app_env,
            )
        yield
        try:
            service = await run_in_threadpool(get_storage_service)
            await run_in_threadpool(service.close)
        except Exception:
            pass

    app = FastAPI(
        title=settings.app_name,
        description="Enterprise Retrieval Augmented Generation API.",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    @app.exception_handler(StorageServiceError)
    async def storage_error_handler(request: Request, exc: StorageServiceError):
        # Log full detail server-side; return a generic message to the client.
        logger.error("Storage error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Storage service is temporarily unavailable"},
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
        """Liveness probe: the process is up. Does not call external services."""
        return {"status": "healthy"}

    @app.get("/ready")
    async def readiness_check():
        """Readiness probe: verifies the GCS bucket is reachable."""
        try:
            service = await run_in_threadpool(get_storage_service)
            ok = await run_in_threadpool(service.ping)
        except Exception:
            logger.exception("Readiness check failed")
            ok = False
        if not ok:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    app.include_router(documents_router)
    return app


app = create_app()
