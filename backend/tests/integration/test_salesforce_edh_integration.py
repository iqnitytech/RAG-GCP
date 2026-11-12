"""Backend real-endpoint integration tests for ``SalesforceEdhStrategy``.

Configure ``SF_INSTANCE_URL``, ``SF_CLIENT_ID``, and ``SF_CLIENT_SECRET`` in
``backend/tests/integration/.env.integration`` and object constants in
``backend/tests/integration/constants.py``.
"""

from __future__ import annotations

import pytest

from app.connectors import create_edh_connector
from app.core.config import get_integration_settings
from app.models.edh_models import DataZone
from tests.integration.constants import (
    DEFAULT_PAGE_SIZE,
    SALESFORCE_CONTENT_VERSION_PREFIX,
    SALESFORCE_SOBJECT_FILTER,
    SALESFORCE_SOBJECT_NAME,
)


@pytest.fixture
def salesforce_settings():
    settings = get_integration_settings()
    if not (
        settings.SF_INSTANCE_URL
        and settings.SF_CLIENT_ID
        and settings.SF_CLIENT_SECRET.get_secret_value()
    ):
        pytest.skip(
            "Populate SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET "
            "in backend/tests/integration/.env.integration"
        )
    return settings


def test_salesforce_ping_and_fetch_sobject_records(salesforce_settings):
    """Connect to the configured Salesforce org via Settings and fetch real SObject records."""
    sobject_name = salesforce_settings.SF_TEST_SOBJECT or SALESFORCE_SOBJECT_NAME
    with create_edh_connector(salesforce_settings, strategy="salesforce") as sf_edh:
        assert sf_edh.ping() is True
        records = list(
            sf_edh.read_records(
                sobject_name,
                zone=DataZone.CURATED,
                partition_filter=SALESFORCE_SOBJECT_FILTER or None,
            )
        )
        assert isinstance(records, list)


def test_salesforce_list_content_version_objects(salesforce_settings):
    """List ContentVersion documents from the configured Salesforce org."""
    with create_edh_connector(salesforce_settings, strategy="salesforce") as sf_edh:
        files_page = sf_edh.list_objects(
            zone=DataZone.RAW,
            prefix=SALESFORCE_CONTENT_VERSION_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(files_page.items, tuple)
