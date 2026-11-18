from __future__ import annotations
from typing import Literal
from pydantic import model_validator
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
