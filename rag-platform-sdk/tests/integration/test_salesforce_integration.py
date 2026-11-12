"""Real-endpoint integration tests for ``SalesforceEdhStrategy``.

Configure credentials in ``rag-platform-sdk/tests/integration/.env.integration``
and target SObject / ContentVersion constants in ``rag-platform-sdk/tests/integration/constants.py``.
"""

from __future__ import annotations

from sdk.connectors.edh import DataZone, EdhConnector
from tests.integration.constants import (
    DEFAULT_PAGE_SIZE,
    SALESFORCE_CONTENT_VERSION_PREFIX,
    SALESFORCE_SOBJECT_FILTER,
    SALESFORCE_SOBJECT_NAME,
)


def test_salesforce_ping_and_fetch_sobject_records(
    salesforce_connector: EdhConnector,
    integration_env: dict[str, str],
):
    """Connect to the configured Salesforce org and query real SObject records."""
    sobject_name = integration_env.get("SF_TEST_SOBJECT") or SALESFORCE_SOBJECT_NAME
    with salesforce_connector as sf:
        assert sf.ping() is True
        records = list(
            sf.read_records(
                sobject_name,
                zone=DataZone.CURATED,
                partition_filter=SALESFORCE_SOBJECT_FILTER or None,
            )
        )
        assert isinstance(records, list)


def test_salesforce_list_content_version_objects(salesforce_connector: EdhConnector):
    """List ContentVersion documents from the configured Salesforce org."""
    with salesforce_connector as sf:
        page = sf.list_objects(
            zone=DataZone.RAW,
            prefix=SALESFORCE_CONTENT_VERSION_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(page.items, tuple)
