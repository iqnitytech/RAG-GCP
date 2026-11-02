"""GCP authentication for this app.

The credential logic lives in the shared SDK (``sdk.auth.gcp``); this module
only binds it to the app's settings and caches the result per scope set.
"""

from functools import lru_cache

from sdk.auth.gcp import build_gcp_credentials

from app.core.config import BACKEND_ROOT, get_settings


@lru_cache(maxsize=8)
def get_gcp_credentials(scopes: tuple[str, ...] | None = None):
    """Return cached GCP credentials for the configured authentication mode.

    Pass an explicit ``scopes`` tuple (hashable) to override the default scopes.

    Raises:
        RuntimeError: If the configuration is invalid or credentials cannot be obtained.
    """
    return build_gcp_credentials(get_settings(), scopes=scopes, base_dir=BACKEND_ROOT)
