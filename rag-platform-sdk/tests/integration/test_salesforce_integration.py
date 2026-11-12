"""Real-endpoint integration tests for ``SalesforceEdhStrategy``.

Configure credentials in ``rag-platform-sdk/tests/integration/.env.integration``,
Salesforce object names in ``rag-platform-sdk/tests/integration/salesforce_objects.py``,
and generic constants in ``rag-platform-sdk/tests/integration/constants.py``.
"""

from __future__ import annotations

from sdk.connectors.edh import DataZone, EdhConnector
from tests.integration.constants import DEFAULT_PAGE_SIZE
from tests.integration.salesforce_objects import SalesforceObjects


def test_salesforce_ping_and_fetch_sobject_records(salesforce_connector: EdhConnector):
    """Connect to the configured Salesforce org and query SObject records from SalesforceObjects."""
    with salesforce_connector as sf:
        assert sf.ping() is True
        records = list(
            sf.read_records(
                SalesforceObjects.TARGET_SOBJECT,
                zone=DataZone.CURATED,
                partition_filter=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
            )
        )
        assert isinstance(records, list)


def test_salesforce_list_content_version_objects(salesforce_connector: EdhConnector):
    """List ContentVersion documents from the configured Salesforce org."""
    with salesforce_connector as sf:
        page = sf.list_objects(
            zone=DataZone.RAW,
            prefix=SalesforceObjects.CONTENT_VERSION_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(page.items, tuple)
