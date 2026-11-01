"""
GCP authentication module.

Supported authentication modes
--------------------------------
wif   – Workload Identity Federation (required in production / staging).
        Two sub-paths:
          a) GCP_WIF_CREDENTIAL_FILE – an external-account JSON key file.
          b) GCP_WIF_PROVIDER + GCP_WIF_SERVICE_ACCOUNT – ADC-style WIF flow.

adc   – Application Default Credentials (allowed only in non-production
        environments; suitable for local developer machines that have run
        ``gcloud auth application-default login``).

The mode is controlled by the GCP_AUTH_MODE environment variable (default: "wif").
In production / staging environments, "adc" is explicitly rejected.
"""

import logging
import os
from functools import lru_cache
from typing import Iterable

from google.auth import default as google_default
from google.auth import impersonated_credentials
from google.auth import load_credentials_from_file

from app.core.config import get_settings

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
    * ``GCP_AUTH_MODE`` must be ``"wif"`` or ``"adc"`` (case-insensitive).
    * ``"adc"`` is rejected in production / staging environments.
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
    if auth_mode not in {"wif", "adc"}:
        raise RuntimeError(
            f"GCP_AUTH_MODE must be 'wif' or 'adc', got '{auth_mode}'."
        )

    # ---- production guard: ADC is not allowed in prod/staging --------------
    if auth_mode == "adc" and _is_production_environment(settings):
        raise RuntimeError(
            "ADC authentication is not allowed in production or staging environments. "
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


@lru_cache(maxsize=1)
def get_gcp_credentials(scopes: tuple[str, ...] | None = None):
    """
    Return GCP credentials for the configured authentication mode.

    The result is cached for the lifetime of the process.  Pass an explicit
    ``scopes`` tuple to override the default scopes read from settings.

    Authentication paths (in priority order for wif mode)
    -------------------------------------------------------
    1. WIF credential file  – ``GCP_WIF_CREDENTIAL_FILE`` is set.
    2. WIF provider flow    – ``GCP_WIF_PROVIDER`` + ``GCP_WIF_SERVICE_ACCOUNT``.
    3. ADC (dev only)       – ``GCP_AUTH_MODE=adc``; rejected in prod/staging.

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

        # Sub-path 1b: WIF provider + service account via ADC discovery
        wif_provider = (settings.gcp_wif_provider or "").strip()
        wif_service_account = (settings.gcp_wif_service_account or "").strip()
        if wif_provider and wif_service_account:
            try:
                credentials, project_id = google_default(scopes=resolved_scopes)
                logger.info(
                    "Loaded WIF credentials via ADC -- provider=%s "
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

    # ---- Path 3: ADC (development only) ------------------------------------
    if auth_mode == "adc":
        try:
            credentials, project_id = google_default(scopes=resolved_scopes)
            logger.info(
                "Using Application Default Credentials (ADC) -- project=%s",
                project_id,
            )
            return _apply_impersonation_if_needed(credentials, settings, resolved_scopes)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to obtain Application Default Credentials: {exc}. "
                "Run 'gcloud auth application-default login' to set up ADC."
            ) from exc

    # Unreachable: _validate_auth_config already rejects unknown modes.
    raise RuntimeError(f"Unrecognised GCP_AUTH_MODE: '{auth_mode}'.")  # pragma: no cover
