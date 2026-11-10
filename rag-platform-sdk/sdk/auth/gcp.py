"""GCP credential resolution.

Settings-agnostic: pass any object exposing the attributes below (e.g. a
pydantic ``Settings``): ``app_env``, ``gcp_project_id``, ``gcp_auth_mode``,
``gcp_wif_credential_file``, ``gcp_wif_provider``, ``gcp_wif_service_account``,
``gcp_credential_file``, ``gcp_impersonate_service_account``,
``gcp_default_scopes``.

Authentication modes
--------------------
wif   - Workload Identity Federation (required in production / staging).
          a) gcp_wif_credential_file: an external-account JSON file.
          b) gcp_wif_provider + gcp_wif_service_account: identity attached to
             the runtime (Cloud Run / GKE).
local - Local development only; rejected in production / staging.
          a) gcp_credential_file empty: your own
             ``gcloud auth application-default login`` identity (no key file).
          b) gcp_credential_file set: that service-account JSON key
             (relative paths resolve against ``base_dir``).

Either mode can additionally impersonate ``gcp_impersonate_service_account``.
"""

import logging
import os
from pathlib import Path
from typing import Any, Iterable

from google.auth import default as google_default
from google.auth import impersonated_credentials, load_credentials_from_file

logger = logging.getLogger(__name__)

_PRODUCTION_ENVS = {"prod", "production", "staging", "stage"}
_DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _normalise_scopes(settings: Any, scopes: Iterable[str] | None) -> list[str]:
    """Explicit scopes > ``gcp_default_scopes`` (comma separated) > cloud-platform."""
    if scopes:
        base = list(scopes)
    else:
        raw = (getattr(settings, "gcp_default_scopes", "") or "").strip()
        base = [s.strip() for s in raw.split(",")] if raw else [_DEFAULT_SCOPE]
    return [s.strip() for s in base if s and s.strip()]


def _is_production_environment(settings: Any) -> bool:
    env_name = (getattr(settings, "app_env", "") or "").lower().strip()
    return env_name in _PRODUCTION_ENVS


def _validate_auth_config(settings: Any) -> None:
    """Raise ``RuntimeError`` when the configuration is incomplete or unsafe."""
    if not (settings.gcp_project_id or "").strip():
        raise RuntimeError("GCP_PROJECT_ID is required for GCP authentication.")

    auth_mode = (settings.gcp_auth_mode or "wif").lower().strip()
    if auth_mode not in {"wif", "local"}:
        raise RuntimeError(f"GCP_AUTH_MODE must be 'wif' or 'local', got '{auth_mode}'.")

    if auth_mode == "local" and _is_production_environment(settings):
        raise RuntimeError(
            "GCP_AUTH_MODE=local is not allowed in production or staging environments. "
            "Set GCP_AUTH_MODE=wif and supply WIF credentials."
        )

    if auth_mode == "wif":
        has_file = bool((settings.gcp_wif_credential_file or "").strip())
        has_provider = bool(
            (settings.gcp_wif_provider or "").strip()
            and (settings.gcp_wif_service_account or "").strip()
        )
        if not has_file and not has_provider:
            raise RuntimeError(
                "WIF authentication requires either:\n"
                "  - GCP_WIF_CREDENTIAL_FILE (path to an external-account JSON file), or\n"
                "  - both GCP_WIF_PROVIDER and GCP_WIF_SERVICE_ACCOUNT."
            )


def _apply_impersonation_if_needed(credentials, settings: Any, scopes: list[str]):
    target = (settings.gcp_impersonate_service_account or "").strip()
    if not target:
        return credentials
    logger.info("Service-account impersonation enabled -- target_principal=%s", target)
    return impersonated_credentials.Credentials(
        source_credentials=credentials,
        target_principal=target,
        target_scopes=scopes,
        lifetime=3600,
    )


def _load_file(path: str, scopes: list[str], label: str):
    try:
        credentials, project_id = load_credentials_from_file(path, scopes=scopes)
    except Exception as exc:
        raise RuntimeError(f"Failed to load {label} credentials from '{path}': {exc}") from exc
    logger.info("Loaded %s credentials from file -- project=%s", label, project_id)
    return credentials


def build_gcp_credentials(
    settings: Any,
    scopes: Iterable[str] | None = None,
    base_dir: Path | None = None,
):
    """Resolve credentials for the configured mode (no caching; cache in the app).

    Raises:
        RuntimeError: If the configuration is invalid or credentials cannot be obtained.
    """
    _validate_auth_config(settings)
    auth_mode = (settings.gcp_auth_mode or "wif").lower().strip()
    resolved = _normalise_scopes(settings, scopes)

    if auth_mode == "wif":
        wif_file = (settings.gcp_wif_credential_file or "").strip()
        if wif_file:
            if not os.path.exists(wif_file):
                raise RuntimeError(f"GCP_WIF_CREDENTIAL_FILE points to a missing file: '{wif_file}'")
            creds = _load_file(wif_file, resolved, "WIF")
            return _apply_impersonation_if_needed(creds, settings, resolved)

        try:
            creds, project_id = google_default(scopes=resolved)
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialise WIF credentials using GCP_WIF_PROVIDER and "
                f"GCP_WIF_SERVICE_ACCOUNT. Underlying error: {exc}"
            ) from exc
        logger.info(
            "Loaded WIF credentials via attached identity -- provider=%s service_account=%s project=%s",
            settings.gcp_wif_provider,
            settings.gcp_wif_service_account,
            project_id,
        )
        return _apply_impersonation_if_needed(creds, settings, resolved)

    # local (development only)
    raw_path = (settings.gcp_credential_file or "").strip()
    if not raw_path:
        try:
            creds, project_id = google_default(scopes=resolved)
        except Exception as exc:
            raise RuntimeError(
                f"No GCP_CREDENTIAL_FILE set and gcloud login not found: {exc}. "
                "Run 'gcloud auth application-default login'."
            ) from exc
        logger.info("Using local gcloud login -- project=%s", project_id)
        return _apply_impersonation_if_needed(creds, settings, resolved)

    key_path = Path(raw_path)
    if not key_path.is_absolute() and base_dir is not None:
        key_path = Path(base_dir) / key_path
    if not key_path.exists():
        raise RuntimeError(f"GCP_CREDENTIAL_FILE points to a missing file: '{key_path}'")
    creds = _load_file(str(key_path), resolved, "local")
    return _apply_impersonation_if_needed(creds, settings, resolved)

