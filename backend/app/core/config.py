
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):

    # Application
    app_name: str = "Enterprise RAG API"
    app_env: str = "development"
    debug: bool = False

    # GCP
    gcp_project_id: str
    gcp_region: str = "asia-south1"

    # Storage
    gcs_document_bucket: str

    # AI
    vertex_ai_enabled: bool = True
    llm_provider: str = "vertex_ai"

    # Vector database
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
