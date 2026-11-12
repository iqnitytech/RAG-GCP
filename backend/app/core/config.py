import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_ENVS = {"prod", "production"}
INTEGRATION_ENVS = {"int", "integration", "test_integration"}


def resolve_env_file() -> Path:
    """
    Pick the dotenv file for the current runtime.

    * APP_ENV=prod|production            -> .env.production (non-secret config only)
    * APP_ENV=int|integration|...        -> .env.integration (integration test config)
    * anything else (default)            -> .env.local

    APP_ENV is read from the real process environment so a deployment or test
    runner can select its file without any file needing to exist. Real
    environment variables (e.g. Cloud Run secrets) always override values from
    the file.
    """
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env in PRODUCTION_ENVS:
        name = ".env.production"
    elif app_env in INTEGRATION_ENVS:
        name = ".env.integration"
    else:
        name = ".env.local"
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
    # Per-process requests/minute per caller; 0 disables rate limiting.
    rate_limit_per_minute: int = Field(
        default=60,
        ge=0,
        validation_alias=AliasChoices("RATE_LIMIT_PER_MINUTE", "rate_limit_per_minute"),
    )

    # Salesforce EDH Connector (optional; secret injected via Secret Manager in production)
    sf_instance_url: str = Field(
        default="",
        validation_alias=AliasChoices("SF_INSTANCE_URL", "sf_instance_url"),
    )
    sf_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("SF_CLIENT_ID", "sf_client_id"),
    )
    sf_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("SF_CLIENT_SECRET", "sf_client_secret"),
    )
    sf_test_sobject: str = Field(
        default="Account",
        validation_alias=AliasChoices("SF_TEST_SOBJECT", "sf_test_sobject"),
    )

    # Databricks EDH Connector (optional; secrets injected via Secret Manager in production)
    databricks_host: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_HOST", "databricks_host"),
    )
    databricks_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DATABRICKS_TOKEN", "databricks_token"),
    )
    databricks_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("DATABRICKS_CLIENT_ID", "databricks_client_id"),
    )
    databricks_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DATABRICKS_CLIENT_SECRET", "databricks_client_secret"),
    )
    databricks_warehouse_id: str = Field(
        default="default",
        validation_alias=AliasChoices("DATABRICKS_WAREHOUSE_ID", "databricks_warehouse_id"),
    )
    databricks_catalog: str = Field(
        default="main",
        validation_alias=AliasChoices("DATABRICKS_CATALOG", "databricks_catalog"),
    )
    databricks_schema: str = Field(
        default="default",
        validation_alias=AliasChoices("DATABRICKS_SCHEMA", "databricks_schema"),
    )
    databricks_test_table: str = Field(
        default="rag_chunks",
        validation_alias=AliasChoices("DATABRICKS_TEST_TABLE", "databricks_test_table"),
    )

    model_config = SettingsConfigDict(
        env_file=resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def SF_INSTANCE_URL(self) -> str:
        return self.sf_instance_url

    @property
    def SF_CLIENT_ID(self) -> str:
        return self.sf_client_id

    @property
    def SF_CLIENT_SECRET(self) -> SecretStr:
        return self.sf_client_secret

    @property
    def SF_TEST_SOBJECT(self) -> str:
        return self.sf_test_sobject

    @property
    def DATABRICKS_HOST(self) -> str:
        return self.databricks_host

    @property
    def DATABRICKS_TOKEN(self) -> SecretStr:
        return self.databricks_token

    @property
    def DATABRICKS_CLIENT_ID(self) -> str:
        return self.databricks_client_id

    @property
    def DATABRICKS_CLIENT_SECRET(self) -> SecretStr:
        return self.databricks_client_secret

    @property
    def DATABRICKS_WAREHOUSE_ID(self) -> str:
        return self.databricks_warehouse_id

    @property
    def DATABRICKS_CATALOG(self) -> str:
        return self.databricks_catalog

    @property
    def DATABRICKS_SCHEMA(self) -> str:
        return self.databricks_schema

    @property
    def DATABRICKS_TEST_TABLE(self) -> str:
        return self.databricks_test_table

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


def get_integration_settings() -> Settings:
    """Load settings from ``backend/tests/integration/.env.integration`` (with fallbacks)."""
    candidates = (
        BACKEND_ROOT / ".env.local",
        BACKEND_ROOT / ".env.integration",
        BACKEND_ROOT / "tests" / "integration" / ".env.integration",
    )
    env_files = tuple(p for p in candidates if p.exists())
    return Settings(_env_file=env_files or None)

