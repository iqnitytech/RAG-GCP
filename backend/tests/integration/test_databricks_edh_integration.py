"""Backend real-endpoint integration tests for ``DatabricksEdhStrategy``.

Configure ``DATABRICKS_HOST`` and ``DATABRICKS_TOKEN`` (or ``DATABRICKS_CLIENT_ID`` +
``DATABRICKS_CLIENT_SECRET``) in ``backend/tests/integration/.env.integration`` and
object constants in ``backend/tests/integration/constants.py``.
"""

from __future__ import annotations

import pytest

from app.connectors import create_edh_connector
from app.core.config import get_integration_settings
from app.models.edh_models import DataZone
from tests.integration.constants import (
    DATABRICKS_TABLE_FILTER,
    DATABRICKS_TABLE_NAME,
    DATABRICKS_VOLUME_PREFIX,
    DEFAULT_PAGE_SIZE,
)


@pytest.fixture
def databricks_settings():
    settings = get_integration_settings()
    has_auth = bool(
        settings.DATABRICKS_TOKEN.get_secret_value()
        or (settings.DATABRICKS_CLIENT_ID and settings.DATABRICKS_CLIENT_SECRET.get_secret_value())
    )
    if not (settings.DATABRICKS_HOST and has_auth):
        pytest.skip(
            "Populate DATABRICKS_HOST + DATABRICKS_TOKEN (or DATABRICKS_CLIENT_ID/SECRET) "
            "in backend/tests/integration/.env.integration"
        )
    return settings


def test_databricks_ping_and_read_uc_table_records(databricks_settings):
    """Connect to the configured Databricks workspace via Settings and query Unity Catalog records."""
    table_name = databricks_settings.DATABRICKS_TEST_TABLE or DATABRICKS_TABLE_NAME
    with create_edh_connector(databricks_settings, strategy="databricks") as dbx_edh:
        assert dbx_edh.ping() is True
        records = list(
            dbx_edh.read_records(
                table_name,
                zone=DataZone.CURATED,
                partition_filter=DATABRICKS_TABLE_FILTER or None,
            )
        )
        assert isinstance(records, list)


def test_databricks_list_unity_catalog_volume_objects(databricks_settings):
    """List files in the configured Databricks Unity Catalog Volume."""
    with create_edh_connector(databricks_settings, strategy="databricks") as dbx_edh:
        files_page = dbx_edh.list_objects(
            zone=DataZone.RAW,
            prefix=DATABRICKS_VOLUME_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(files_page.items, tuple)
