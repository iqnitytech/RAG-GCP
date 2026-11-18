from __future__ import annotations
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientSettings(BaseSettings):
    """Configuration for the MCP Client Diagnostic Dashboard and Runner."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    CLIENT_HOST: str = "127.0.0.1"
    CLIENT_PORT: int = 8000
    DATA_PROVIDER_MODE: Literal["MOCK", "SDK"] = "MOCK"
    DEFAULT_TIMEOUT_SECONDS: float = 15.0
    DEBUG: bool = True
