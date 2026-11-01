"""
GCP authentication module.

Supported authentication modes
--------------------------------
wif   - Workload Identity Federation (required in production / staging).
        Two sub-paths:
          a) GCP_WIF_CREDENTIAL_FILE - an external-account JSON file.
          b) GCP_WIF_PROVIDER + GCP_WIF_SERVICE_ACCOUNT - identity attached to
             the runtime (Cloud Run / GKE).

local - Local development only. Rejected in production / staging.
          a) GCP_CREDENTIAL_FILE empty (default) - uses your own
             ``gcloud auth application-default login`` identity; no key file.
             Combine with GCP_IMPERSONATE_SERVICE_ACCOUNT so calls run as a
             low-privilege dev service account.
          b) GCP_CREDENTIAL_FILE set - uses that service-account JSON key
             (relative paths resolve from backend/).

The mode is controlled by GCP_AUTH_MODE (default: "wif").
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from google.auth import default as google_default
from google.auth import impersonated_credentials
from google.auth import load_credentials_from_file

from app.core.config import BACKEND_ROOT, get_settings

logger = logging.getLogger(__name__)


def _normalise_scopes(scopes: Iterable[str] | None) -> list[str]:
    """
    Resolve the OAuth2 scopes to use for GCP credentials.

    Priority:
      1. Explicit ``scopes`` argument (caller-supplied).
      2. ``GCP_DEFAULT_SCOPES`` setting (comma-separated string).
      3. Fallback: ``https://www.googleapis.com/auth/cloud-platform``.
    """
    settings = get_settings()

    if scopes:
        base_scopes = list(scopes)
    else:
        raw_scope_value = (settings.gcp_default_scopes or "").strip()
        if raw_scope_value:
            base_scopes = [
                item.strip()
                for item in raw_scope_value.split(",")
                if item and item.strip()
            ]
        else:
            base_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    return [scope.strip() for scope in base_scopes if scope and scope.strip()]


def _is_production_environment(settings) -> bool:
    """Return True when the runtime environment is production or staging."""
    env_name = (getattr(settings, "app_env", "") or "").lower().strip()
    return env_name in {"prod", "production", "staging", "stage"}


def _validate_auth_config(settings) -> None:
    """
    Validate GCP authentication configuration and raise ``RuntimeError``
    with a descriptive message when the configuration is incomplete or
    incompatible with the current environment.

    Rules
    -----
    * ``GCP_PROJECT_ID`` is always required.
    * ``GCP_AUTH_MODE`` must be ``"wif"`` or ``"local"`` (case-insensitive).
    * ``"local"`` is rejected in production / staging environments.
    * ``"local"`` uses ``GCP_CREDENTIAL_FILE`` if set, otherwise gcloud login.
    * ``"wif"`` requires at least one of:
        - ``GCP_WIF_CREDENTIAL_FILE`` (path to an external-account JSON file), **or**
        - both ``GCP_WIF_PROVIDER`` **and** ``GCP_WIF_SERVICE_ACCOUNT``.
    """
    # ---- project ID --------------------------------------------------------
    project_id = (settings.gcp_project_id or "").strip()
    if not project_id:
        raise RuntimeError("GCP_PROJECT_ID is required for GCP authentication.")

    # ---- auth mode ---------------------------------------------------------
    auth_mode = (settings.gcp_auth_mode or "wif").lower().strip()
    if auth_mode not in {"wif", "local"}:
        raise RuntimeError(
            f"GCP_AUTH_MODE must be 'wif' or 'local', got '{auth_mode}'."
        )

    # ---- production guard: local key files are not allowed in prod/staging -
    if auth_mode == "local" and _is_production_environment(settings):
        raise RuntimeError(
            "GCP_AUTH_MODE=local is not allowed in production or staging environments. "
            "Set GCP_AUTH_MODE=wif and supply WIF credentials."
        )

    # ---- WIF-specific validation -------------------------------------------
    if auth_mode == "wif":
        wif_credential_file = (settings.gcp_wif_credential_file or "").strip()
        wif_provider = (settings.gcp_wif_provider or "").strip()
        wif_service_account = (settings.gcp_wif_service_account or "").strip()

        has_credential_file = bool(wif_credential_file)
        has_provider_flow = bool(wif_provider and wif_service_account)

        if not has_credential_file and not has_provider_flow:
            raise RuntimeError(
                "WIF authentication requires either:\n"
                "  - GCP_WIF_CREDENTIAL_FILE (path to an external-account JSON file), or\n"
                "  - both GCP_WIF_PROVIDER and GCP_WIF_SERVICE_ACCOUNT."
            )


def _apply_impersonation_if_needed(credentials, settings, scopes: list[str]):
    """
    Wrap *credentials* with service-account impersonation when
    ``GCP_IMPERSONATE_SERVICE_ACCOUNT`` is configured.

    Args:
        credentials: The base GCP credentials object.
        settings:    App settings instance.
        scopes:      OAuth2 scopes to use for the impersonated credentials.

    Returns:
        Either the original credentials or an
        ``google.auth.impersonated_credentials.Credentials`` wrapper.
    """
    target_principal = (settings.gcp_impersonate_service_account or "").strip()
    if not target_principal:
        return credentials

    impersonated = impersonated_credentials.Credentials(
        source_credentials=credentials,
        target_principal=target_principal,
        target_scopes=scopes,
        lifetime=3600,
    )
    logger.info(
        "Service-account impersonation enabled -- target_principal=%s",
        target_principal,
    )
    return impersonated


@lru_cache(maxsize=8)
def get_gcp_credentials(scopes: tuple[str, ...] | None = None):
    """
    Return GCP credentials for the configured authentication mode.

    The result is cached for the lifetime of the process.  Pass an explicit
    ``scopes`` tuple to override the default scopes read from settings.

    Authentication paths (in priority order for wif mode)
    -------------------------------------------------------
    1. WIF credential file  â€“ ``GCP_WIF_CREDENTIAL_FILE`` is set.
    2. WIF provider flow    â€“ ``GCP_WIF_PROVIDER`` + ``GCP_WIF_SERVICE_ACCOUNT``.
    3. Local (dev only)     - GCP_AUTH_MODE=local; gcloud login or GCP_CREDENTIAL_FILE;
                              rejected in prod/staging.

    Raises:
        RuntimeError: If the configuration is invalid or credentials cannot
                      be obtained.
    """
    settings = get_settings()
    _validate_auth_config(settings)

    auth_mode = (settings.gcp_auth_mode or "wif").lower().strip()
    resolved_scopes = _normalise_scopes(scopes)

    # ---- Path 1 & 2: WIF ---------------------------------------------------
    if auth_mode == "wif":
        wif_credential_file = (settings.gcp_wif_credential_file or "").strip()

        # Sub-path 1a: external-account JSON credential file
        if wif_credential_file:
            if not os.path.exists(wif_credential_file):
                raise RuntimeError(
                    f"GCP_WIF_CREDENTIAL_FILE points to a missing file: "
                    f"'{wif_credential_file}'"
                )
            try:
                credentials, project_id = load_credentials_from_file(
                    wif_credential_file,
                    scopes=resolved_scopes,
                )
                logger.info(
                    "Loaded WIF credentials from file -- path=%s project=%s",
                    wif_credential_file,
                    project_id,
                )
                return _apply_impersonation_if_needed(credentials, settings, resolved_scopes)
            except RuntimeError:
                raise  # re-raise our own errors unchanged
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load WIF credentials from "
                    f"'{wif_credential_file}': {exc}"
                ) from exc

        # Sub-path 1b: WIF provider + service account via the runtime-attached identity
        wif_provider = (settings.gcp_wif_provider or "").strip()
        wif_service_account = (settings.gcp_wif_service_account or "").strip()
        if wif_provider and wif_service_account:
            try:
                credentials, project_id = google_default(scopes=resolved_scopes)
                logger.info(
                    "Loaded WIF credentials via attached identity -- provider=%s "
                    "service_account=%s project=%s",
                    wif_provider,
                    wif_service_account,
                    project_id,
                )
                return _apply_impersonation_if_needed(credentials, settings, resolved_scopes)
            except Exception as exc:
                raise RuntimeError(
                    "Failed to initialise WIF credentials using "
                    "GCP_WIF_PROVIDER and GCP_WIF_SERVICE_ACCOUNT. "
                    f"Underlying error: {exc}"
                ) from exc

        # Unreachable: _validate_auth_config already ensured one path exists.
        raise RuntimeError(  # pragma: no cover
            "No valid WIF configuration found. Provide GCP_WIF_CREDENTIAL_FILE "
            "or both GCP_WIF_PROVIDER and GCP_WIF_SERVICE_ACCOUNT."
        )

    # ---- Path 3: local development only ------------------------------------
    if auth_mode == "local":
        raw_path = (settings.gcp_credential_file or "").strip()

        # 3a: no key file -> developer's own gcloud login (no secret on disk)
        if not raw_path:
            try:
                credentials, project_id = google_default(scopes=resolved_scopes)
            except Exception as exc:
                raise RuntimeError(
                    f"No GCP_CREDENTIAL_FILE set and gcloud login not found: {exc}. "
                    "Run 'gcloud auth application-default login'."
                ) from exc
            logger.info("Using local gcloud login -- project=%s", project_id)
            return _apply_impersonation_if_needed(credentials, settings, resolved_scopes)

        # 3b: service-account JSON key
        key_path = Path(raw_path)
        if not key_path.is_absolute():
            key_path = BACKEND_ROOT / key_path
        if not key_path.exists():
            raise RuntimeError(
                f"GCP_CREDENTIAL_FILE points to a missing file: '{key_path}'"
            )
        try:
            credentials, project_id = load_credentials_from_file(
                str(key_path), scopes=resolved_scopes
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load credentials from '{key_path}': {exc}"
            ) from exc
        logger.info("Using local credential file -- project=%s", project_id)
        return _apply_impersonation_if_needed(credentials, settings, resolved_scopes)

    # Unreachable: _validate_auth_config already rejects unknown modes.
    raise RuntimeError(f"Unrecognised GCP_AUTH_MODE: '{auth_mode}'.")  # pragma: no cover
