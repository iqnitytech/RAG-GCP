from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatAppSettings(BaseSettings):
    """Configuration for MCP Chat Agent web application."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    CHAT_HOST: str = "127.0.0.1"
    CHAT_PORT: int = 8050
    DEFAULT_MODE: str = "MOCK"
    DEBUG: bool = True
