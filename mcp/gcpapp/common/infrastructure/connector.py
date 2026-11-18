"""Enterprise Data Hub (EDH) connector factory bound to gcpapp Settings.

Provides reusable connector factory methods for both synchronous and
asynchronous EDH and Salesforce connections across MCP adapters.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from config.settings import Settings
from core.exceptions import AuthenticationError, SdkGatewayError
from core.logging import get_logger

logger = get_logger(__name__)

_SALESFORCE_STRATEGIES = {"salesforce", "sfdc", "salesforce_crm"}
_API_HUB_STRATEGIES = {"api_hub", "edh", "edh_api"}


def build_salesforce_token_getter(
    settings: Settings,
    *,
    instance_url: Optional[str] = None,
) -> Callable[[], str]:
    """Return a callable that resolves a Salesforce OAuth/access token.

    Prefers a static ``SF_ACCESS_TOKEN``; falls back to OAuth client credentials
    using ``SF_CLIENT_ID``, ``SF_CLIENT_SECRET``, and ``instance_url``.
    """
    from sdk.connectors.edh import make_salesforce_oauth_token_getter

    access_token = settings.SF_ACCESS_TOKEN.get_secret_value().strip()
    if access_token and not access_token.lower().startswith("your-"):
        return lambda: settings.SF_ACCESS_TOKEN.get_secret_value()

    effective_url = instance_url or settings.SF_INSTANCE_URL
    if not (effective_url and settings.SF_CLIENT_ID and settings.SF_CLIENT_SECRET.get_secret_value()):
        raise SdkGatewayError(
            "Salesforce is not fully configured. "
            "Set SF_INSTANCE_URL and either SF_ACCESS_TOKEN or "
            "SF_CLIENT_ID + SF_CLIENT_SECRET in your environment."
        )

    return make_salesforce_oauth_token_getter(
        instance_url=effective_url,
        client_id_getter=lambda: settings.SF_CLIENT_ID,
        client_secret_getter=lambda: settings.SF_CLIENT_SECRET.get_secret_value(),
    )


def create_connector(
    settings: Settings,
    *,
    strategy: str = "api_hub",
    namespace: str = "customer",
    endpoint_url: str = "",
    auth_token_getter: Optional[Callable[[], str]] = None,
) -> Any:
    """Build a synchronous :class:`EdhConnector` configured from application settings.

    Parameters
    ----------
    settings:
        Application settings instance.
    strategy:
        Target connector strategy (e.g. ``"salesforce"`` or ``"api_hub"``).
    namespace:
        Logical data namespace (e.g. ``"customer"``, ``"machine"``, ``"telemetry"``).
    endpoint_url:
        Optional override for the endpoint URL.
    auth_token_getter:
        Optional pre-built token getter callable.
    """
    from sdk.connectors.edh import EdhConnector
    from sdk.models.edh_models import EdhConnectorConfig

    norm_strategy = strategy.strip().lower()

    if norm_strategy in _SALESFORCE_STRATEGIES:
        if not settings.is_salesforce_configured and auth_token_getter is None:
            raise SdkGatewayError(
                "Salesforce is not fully configured. "
                "Set SF_INSTANCE_URL and either SF_ACCESS_TOKEN or "
                "SF_CLIENT_ID + SF_CLIENT_SECRET in your environment."
            )

        effective_endpoint = endpoint_url or settings.SF_INSTANCE_URL
        effective_token_getter = (
            auth_token_getter
            if auth_token_getter is not None
            else build_salesforce_token_getter(settings, instance_url=effective_endpoint)
        )

        config = EdhConnectorConfig(
            strategy="salesforce",
            namespace=namespace,
            endpoint_url=effective_endpoint,
            auth_token_getter=effective_token_getter,
        )
        logger.info("EdhConnector initialized for Salesforce", namespace=namespace, endpoint=effective_endpoint)
        return EdhConnector(config)

    if norm_strategy in _API_HUB_STRATEGIES:
        if not settings.SDK_API_KEY:
            raise AuthenticationError(
                "SDK_API_KEY is not configured or empty. "
                "Please set SDK_API_KEY in your environment."
            )
        effective_endpoint = endpoint_url or settings.SDK_BASE_URL
        if not effective_endpoint:
            raise SdkGatewayError(
                "SDK_BASE_URL is not configured or empty. "
                "Please set SDK_BASE_URL in your environment."
            )

        def _get_api_key() -> str:
            key = settings.SDK_API_KEY
            if not key:
                raise AuthenticationError("SDK_API_KEY is not configured or empty")
            return key

        config = EdhConnectorConfig(
            strategy="api_hub",
            namespace=namespace,
            endpoint_url=effective_endpoint,
            api_key_getter=_get_api_key,
            timeout_seconds=float(settings.SDK_TIMEOUT_SECONDS),
        )
        logger.info("EdhConnector initialized for API Hub", namespace=namespace)
        return EdhConnector(config)

    raise ValueError(f"Unsupported connector strategy: {strategy}")


def create_async_edh_connector(
    settings: Settings,
    *,
    strategy: str = "salesforce",
    namespace: str = "customer",
    endpoint_url: str = "",
    auth_token_getter: Optional[Callable[[], str]] = None,
) -> Any:
    """Build an :class:`AsyncEdhConnector` configured from application settings."""
    from sdk.connectors.edh import AsyncEdhConnector

    sync_conn = create_connector(
        settings,
        strategy=strategy,
        namespace=namespace,
        endpoint_url=endpoint_url,
        auth_token_getter=auth_token_getter,
    )
    return AsyncEdhConnector(sync_conn.config, strategy=sync_conn.strategy)
