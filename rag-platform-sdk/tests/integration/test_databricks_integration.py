"""Real-endpoint integration tests for ``DatabricksEdhStrategy``.

Configure credentials in ``rag-platform-sdk/tests/integration/.env.integration``
and target Unity Catalog table / Volume constants in ``rag-platform-sdk/tests/integration/constants.py``.
"""

from __future__ import annotations

from sdk.connectors.edh import DataZone, EdhConnector
from tests.integration.constants import (
    DATABRICKS_TABLE_FILTER,
    DATABRICKS_TABLE_NAME,
    DATABRICKS_VOLUME_PREFIX,
    DEFAULT_PAGE_SIZE,
)


def test_databricks_ping_and_read_uc_table_records(
    databricks_connector: EdhConnector,
    integration_env: dict[str, str],
):
    """Connect to the configured Databricks workspace and query records from a Unity Catalog table."""
    table_name = integration_env.get("DATABRICKS_TEST_TABLE") or DATABRICKS_TABLE_NAME
    with databricks_connector as dbx:
        assert dbx.ping() is True
        records = list(
            dbx.read_records(
                table_name,
                zone=DataZone.CURATED,
                partition_filter=DATABRICKS_TABLE_FILTER or None,
            )
        )
        assert isinstance(records, list)


def test_databricks_list_unity_catalog_volume_objects(databricks_connector: EdhConnector):
    """List files in the configured Databricks Unity Catalog Volume."""
    with databricks_connector as dbx:
        page = dbx.list_objects(
            zone=DataZone.RAW,
            prefix=DATABRICKS_VOLUME_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(page.items, tuple)
