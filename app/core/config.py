from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MyApp"
    app_env: str = "uvicorn app.main:app --reload - app run"
    debug: bool = True

    gcp_project_id: str = "your-gcp-project-id"
    gcp_region: str = "your-gcp-region"
    gcp_document_bucket: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GCP_DOCUMENT_BUCKET",
            "GCS_DOCUMENT_BUCKET",
            "gcp_document_bucket",
        ),
    )
    vertex_ai_enabled: bool = True

    qdrant_url: str = ""
    qdrant_api_key: str = ""

    llm_provider: str = "vertext_ai"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()