from __future__ import annotations
from typing import Literal
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    DATA_PROVIDER_MODE: str = "MOCK"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["JSON", "TEXT"] = "JSON"
    SDK_API_KEY: str = ""
    SDK_BASE_URL: str = ""
    SDK_TENANT_ID: str = ""
    SDK_TIMEOUT_SECONDS: int = 30
    SDK_MAX_RETRIES: int = 3
    CUSTOMER_MCP_PORT: int = 8001
    MACHINE_MCP_PORT: int = 8002
    TELEMETRY_MCP_PORT: int = 8003

    # Salesforce connector settings
    SF_INSTANCE_URL: str = ""
    SF_CLIENT_ID: str = ""
    SF_CLIENT_SECRET: SecretStr = SecretStr("")
    SF_ACCESS_TOKEN: SecretStr = SecretStr("")
    SF_API_VERSION: str = "v60.0"

    @property
    def is_salesforce_configured(self) -> bool:
        """True when enough Salesforce credentials are present to build a connector."""
        has_token = bool(
            self.SF_ACCESS_TOKEN.get_secret_value().strip()
            and not self.SF_ACCESS_TOKEN.get_secret_value().strip().lower().startswith("your-")
        )
        has_oauth = bool(
            self.SF_INSTANCE_URL
            and self.SF_CLIENT_ID
            and self.SF_CLIENT_SECRET.get_secret_value().strip()
        )
        return bool(self.SF_INSTANCE_URL) and (has_token or has_oauth)
