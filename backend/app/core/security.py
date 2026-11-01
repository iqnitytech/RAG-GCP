from secrets import compare_digest

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: str | None = Security(_api_key_header)) -> None:
    """Authenticate callers with a shared API key (header ``X-API-Key``).

    In production the key is mandatory (enforced at startup by the settings
    validator). When no key is configured in development the check is skipped.
    """
    expected = get_settings().api_key.get_secret_value()
    if not expected:
        return
    if not provided or not compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

