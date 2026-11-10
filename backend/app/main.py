import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from sdk import __version__ as sdk_version
from sdk import create_base_app, require_sdk_version

from app.api.documents.documents import router as documents_router
from app.core.config import get_settings
from app.models.request.validations.document_upload_validation import MAX_REQUEST_SIZE
from app.services.storage.storage_service import get_storage_service

logger = logging.getLogger(__name__)

# This app is built against SDK 0.2.x; refuse to start on an incompatible SDK.
require_sdk_version(minimum="0.2.0", below="0.3.0")


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
            logger.info("Storage service ready (sdk=%s)", sdk_version)
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

    async def storage_ready() -> bool:
        """Readiness: the GCS bucket must be reachable."""
        service = await run_in_threadpool(get_storage_service)
        return await run_in_threadpool(service.ping)

    # Logging, middleware, error handlers and /health + /ready come from the SDK.
    app = create_base_app(
        title=settings.app_name,
        description="Enterprise Retrieval Augmented Generation API.",
        version="1.0.0",
        is_production=settings.is_production,
        debug=settings.debug,
        service_name="rag-gcp-backend",
        lifespan=lifespan,
        readiness_check=storage_ready,
        route_body_limits={"/documents/upload": MAX_REQUEST_SIZE},
        require_content_length_paths=("/documents/upload",),
        max_body_bytes_message="Maximum file size is 10 MB",
    )

    @app.get("/")
    async def read_root():
        if settings.is_production:
            return {"message": "Enterprise RAG API is running!"}
        return {
            "message": "Enterprise RAG API is running!",
            "environment": settings.app_env,
            "sdk_version": sdk_version,
            "project_id": settings.gcp_project_id,
            "region": settings.gcp_region,
        }

    app.include_router(documents_router)
    return app


app = create_app()
