from __future__ import annotations

from domain.ports import SalesforcePort
from settings import Settings

from .mock import MockSalesforce


def build_salesforce(settings: Settings) -> SalesforcePort:
    if settings.sf_mode == "mock":
        return MockSalesforce()
    if settings.sf_mode == "sdk":
        from simple_salesforce import Salesforce  # optional dependency

        from .sdk import SalesforceSdkClient

        sf = Salesforce(
            username=settings.sf_username,
            password=settings.sf_password,
            security_token=settings.sf_security_token,
            domain=settings.sf_domain,
        )
        return SalesforceSdkClient(sf)
    raise ValueError(f"Unknown SF_MODE: {settings.sf_mode}")
