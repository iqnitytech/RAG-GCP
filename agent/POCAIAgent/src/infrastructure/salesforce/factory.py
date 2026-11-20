from __future__ import annotations

import logging

from domain.ports import SalesforcePort
from settings import Settings

from .mock import MockSalesforce

logger = logging.getLogger("infrastructure.salesforce.factory")


def build_salesforce(settings: Settings) -> SalesforcePort:
    mode = (settings.sf_mode or "mock").lower()
    if mode == "mock":
        return MockSalesforce()

    if mode == "sdk":
        from .sdk import SalesforceSdkClient

        # Project 1 uses rag-platform-sdk (EdhConnector) for Salesforce integration
        try:
            from sdk.connectors.edh import EdhConnector, make_salesforce_oauth_token_getter
            from sdk.models.edh_models import EdhConnectorConfig

            token = settings.sf_access_token.strip()
            if token and not token.lower().startswith("your-"):
                token_getter = lambda: settings.sf_access_token
            elif settings.sf_instance_url and settings.sf_client_id:
                token_getter = make_salesforce_oauth_token_getter(
                    instance_url=settings.sf_instance_url,
                    client_id_getter=lambda: settings.sf_client_id,
                    client_secret_getter=lambda: settings.sf_client_secret,
                )
            else:
                token_getter = lambda: "mock-token"

            config = EdhConnectorConfig(
                strategy="salesforce",
                namespace="salesforce",
                endpoint_url=settings.sf_instance_url or "https://login.salesforce.com",
                auth_token_getter=token_getter,
            )
            connector = EdhConnector(config)
            return SalesforceSdkClient(connector)
        except ImportError:
            try:
                from simple_salesforce import Salesforce

                sf = Salesforce(
                    username=settings.sf_username,
                    password=settings.sf_password,
                    security_token=settings.sf_security_token,
                    domain=settings.sf_domain,
                )
                return SalesforceSdkClient(sf)
            except ImportError:
                logger.warning("Neither rag-platform-sdk nor simple_salesforce available; falling back to MockSalesforce")
                return MockSalesforce()

    raise ValueError(f"Unknown SF_MODE: {settings.sf_mode}")
