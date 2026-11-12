"""Backend real-endpoint integration tests for ``SalesforceEdhStrategy``.

Configure ``SF_INSTANCE_URL``, ``SF_CLIENT_ID``, and ``SF_CLIENT_SECRET`` in
``backend/tests/integration/.env.integration``, Salesforce object names in
``backend/tests/integration/salesforce_objects.py``, and generic constants in
``backend/tests/integration/constants.py``.
"""

from __future__ import annotations

import pytest

from app.connectors import create_edh_connector
from app.core.config import get_integration_settings
from app.models.edh_models import DataZone
from tests.integration.constants import DEFAULT_PAGE_SIZE
from tests.integration.salesforce_objects import SalesforceObjects


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
    """Connect to the configured Salesforce org via Settings and fetch SObject records."""
    with create_edh_connector(salesforce_settings, strategy="salesforce") as sf_edh:
        assert sf_edh.ping() is True
        records = list(
            sf_edh.read_records(
                SalesforceObjects.TARGET_SOBJECT,
                zone=DataZone.CURATED,
                partition_filter=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
            )
        )
        assert isinstance(records, list)


def test_salesforce_list_content_version_objects(salesforce_settings):
    """List ContentVersion documents from the configured Salesforce org."""
    with create_edh_connector(salesforce_settings, strategy="salesforce") as sf_edh:
        files_page = sf_edh.list_objects(
            zone=DataZone.RAW,
            prefix=SalesforceObjects.CONTENT_VERSION_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(files_page.items, tuple)
