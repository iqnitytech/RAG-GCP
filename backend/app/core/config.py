import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_ENVS = {"prod", "production"}


def resolve_env_file() -> Path:
    """
    Pick the dotenv file for the current runtime.

    * APP_ENV=prod|production -> .env.production (non-secret config only)
    * anything else (default)  -> .env.local

    APP_ENV is read from the real process environment so a deployment can
    select its file without any file needing to exist. Real environment
    variables (e.g. Cloud Run secrets) always override values from the file.
    """
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    name = ".env.production" if app_env in PRODUCTION_ENVS else ".env.local"
    return BACKEND_ROOT / name


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
    # Local development only: path to a service-account JSON key.
    gcp_credential_file: str = Field(
        default="",
        validation_alias=AliasChoices("GCP_CREDENTIAL_FILE", "gcp_credential_file"),
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
    # Secret: in production it is injected by the runtime (Secret Manager).
    qdrant_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("QDRANT_API_KEY", "qdrant_api_key"),
    )

    # Secret: shared API key for the X-API-Key header. Mandatory in production.
    api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("API_KEY", "api_key"),
    )

    model_config = SettingsConfigDict(
        env_file=resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVS

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        """Fail fast on unsafe or incomplete production configuration."""
        if not self.is_production:
            return self
        problems = []
        if self.debug:
            problems.append("DEBUG must be false")
        if self.gcp_auth_mode.strip().lower() != "wif":
            problems.append("GCP_AUTH_MODE must be 'wif'")
        if not self.gcp_project_id:
            problems.append("GCP_PROJECT_ID is required")
        if not self.gcs_document_bucket:
            problems.append("GCS_DOCUMENT_BUCKET is required")
        if not self.qdrant_url:
            problems.append("QDRANT_URL is required")
        if not self.qdrant_api_key.get_secret_value():
            problems.append(
                "QDRANT_API_KEY must be injected by the runtime "
                "(Secret Manager -> env var), not stored in a file"
            )
        if not self.api_key.get_secret_value():
            problems.append("API_KEY must be injected by the runtime (Secret Manager)")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
