from fastapi import FastAPI
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    description="Enterprise Retrieval Augmented Generation API.",
    version="1.0.0"
)

@app.get("/")
async def read_root():
    return {
        "message": "Enterprise RAG API is running!",
        "environment": settings.app_env,
        "project_id": settings.gcp_project_id,
        "region": settings.gcp_region,
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
