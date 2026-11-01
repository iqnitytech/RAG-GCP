from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):

    # Application
    app_name: str = Field(
        default="Enterprise RAG API",
        validation_alias=AliasChoices("APP_NAME", "app_name"),
    )
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "app_env"),
    )
    debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("DEBUG", "debug"),
    )

    # GCP
    gcp_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("GCP_PROJECT_ID", "gcp_project_id"),
    )
    gcp_region: str = Field(
        default="asia-south1",
        validation_alias=AliasChoices("GCP_REGION", "gcp_region"),
    )
    gcp_auth_mode: str = Field(
        default="wif",
        validation_alias=AliasChoices("GCP_AUTH_MODE", "gcp_auth_mode"),
    )
    gcp_wif_provider: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GCP_WIF_PROVIDER",
            "gcp_wif_provider",
        ),
    )
    gcp_wif_service_account: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GCP_WIF_SERVICE_ACCOUNT",
            "gcp_wif_service_account",
        ),
    )
    gcp_wif_credential_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GCP_WIF_CREDENTIAL_FILE",
            "gcp_wif_credential_file",
        ),
    )
    gcp_impersonate_service_account: str = Field(
        default="",
        validation_alias=AliasChoices(
            "GCP_IMPERSONATE_SERVICE_ACCOUNT",
            "gcp_impersonate_service_account",
        ),
    )
    gcp_default_scopes: str = Field(
        default="https://www.googleapis.com/auth/cloud-platform",
        validation_alias=AliasChoices(
            "GCP_DEFAULT_SCOPES",
            "gcp_default_scopes",
        ),
    )

    # Storage
    gcs_document_bucket: str = Field(
        default="",
        validation_alias=AliasChoices("GCS_DOCUMENT_BUCKET", "GCP_DOCUMENT_BUCKET", "gcs_document_bucket"),
    )

    # AI
    vertex_ai_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("VERTEX_AI_ENABLED", "vertex_ai_enabled"),
    )
    llm_provider: str = Field(
        default="vertex_ai",
        validation_alias=AliasChoices("LLM_PROVIDER", "llm_provider"),
    )

    # Vector database
    qdrant_url: str = Field(
        default="",
        validation_alias=AliasChoices("QDRANT_URL", "qdrant_url"),
    )
    qdrant_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("QDRANT_API_KEY", "qdrant_api_key"),
    )

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
