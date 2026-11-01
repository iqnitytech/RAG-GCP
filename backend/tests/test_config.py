import pytest
from pydantic import ValidationError

from app.core.config import Settings, resolve_env_file


def _prod(**overrides):
    base = dict(
        APP_ENV="production",
        DEBUG=False,
        GCP_AUTH_MODE="wif",
        GCP_PROJECT_ID="p",
        GCS_DOCUMENT_BUCKET="b",
        QDRANT_URL="https://q",
        QDRANT_API_KEY="qk",
        API_KEY="ak",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_valid_production_config():
    assert _prod().is_production


@pytest.mark.parametrize(
    "override",
    [
        {"DEBUG": True},
        {"GCP_AUTH_MODE": "local"},
        {"QDRANT_API_KEY": ""},
        {"API_KEY": ""},
        {"GCS_DOCUMENT_BUCKET": ""},
    ],
)
def test_unsafe_production_config_is_rejected(override):
    with pytest.raises(ValidationError):
        _prod(**override)


def test_env_file_selection(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert resolve_env_file().name == ".env.production"
    monkeypatch.setenv("APP_ENV", "development")
    assert resolve_env_file().name == ".env.local"

