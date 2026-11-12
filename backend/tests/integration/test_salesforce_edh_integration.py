"""Backend real-endpoint integration tests for ``SalesforceEdhStrategy``.

Configure ``SF_INSTANCE_URL``, ``SF_CLIENT_ID``, and ``SF_CLIENT_SECRET`` in
``backend/tests/integration/.env.integration``, Salesforce object names/payloads in
``backend/tests/integration/salesforce_objects.py``, and generic constants in
``backend/tests/integration/constants.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from app.connectors import create_async_edh_connector, create_edh_connector
from app.core.config import get_integration_settings
from app.models.edh_models import DataZone
from tests.integration.constants import DEFAULT_PAGE_SIZE
from tests.integration.salesforce_objects import SalesforceObjects


@pytest.fixture
def salesforce_settings():
    settings = get_integration_settings()
    if not settings.is_salesforce_configured:
        pytest.skip(
            "Populate real SF_INSTANCE_URL, SF_CLIENT_ID, and SF_CLIENT_SECRET "
            "in backend/tests/integration/.env.integration"
        )
    return settings


def test_salesforce_ping_and_fetch_sobject_records(salesforce_settings):
    """Connect to the configured Salesforce org via Settings and fetch SObject records."""
    connector = create_edh_connector(salesforce_settings, strategy="salesforce")
    assert connector.is_open is False
    with connector as sf_edh:
        assert sf_edh.is_open is True
        assert sf_edh.ping() is True
        records = list(
            sf_edh.read_records(
                SalesforceObjects.TARGET_SOBJECT,
                zone=DataZone.CURATED,
                partition_filter=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
            )
        )
        assert isinstance(records, list)
    assert connector.is_open is False


def test_salesforce_list_content_version_objects(salesforce_settings):
    """List ContentVersion documents from the configured Salesforce org."""
    connector = create_edh_connector(salesforce_settings, strategy="salesforce")
    with connector as sf_edh:
        assert sf_edh.is_open is True
        files_page = sf_edh.list_objects(
            zone=DataZone.RAW,
            prefix=SalesforceObjects.CONTENT_VERSION_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(files_page.items, tuple)
    assert connector.is_open is False


def test_salesforce_1_describe_schema_2_query_3_paginated_query(salesforce_settings):
    """Test 1. DescribeSchema, 2. Query, and 3. PaginatedQuery via Backend EDH connector."""
    connector = create_edh_connector(salesforce_settings, strategy="salesforce")
    with connector as sf_edh:
        assert sf_edh.is_open is True
        schema = sf_edh.DescribeSchema(SalesforceObjects.TARGET_SOBJECT)
        assert schema.get("name") == SalesforceObjects.TARGET_SOBJECT

        records = sf_edh.Query(
            SalesforceObjects.TARGET_SOBJECT,
            fields=SalesforceObjects.SELECT_FIELDS,
            where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
            limit=SalesforceObjects.QUERY_LIMIT,
        )
        assert isinstance(records, list)

        pages = list(
            sf_edh.PaginatedQuery(
                SalesforceObjects.TARGET_SOBJECT,
                fields=SalesforceObjects.SELECT_FIELDS,
                where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
                limit=SalesforceObjects.QUERY_LIMIT,
                batch_size=SalesforceObjects.PAGINATED_BATCH_SIZE,
            )
        )
        assert isinstance(pages, list)
    assert connector.is_open is False


def test_salesforce_4_get_5_create_record_6_update_7_upsert_8_delete_record(salesforce_settings):
    """Test 4. GetRecordById, 5. createRecord, 6. Update, 7. Upsert, and 8. deleteRecord via Backend EDH connector."""
    connector = create_edh_connector(salesforce_settings, strategy="salesforce")
    with connector as sf_edh:
        assert sf_edh.is_open is True
        created = sf_edh.createRecord(
            SalesforceObjects.TARGET_SOBJECT,
            SalesforceObjects.CREATE_PAYLOAD,
        )
        assert created.get("success") is True
        record_id = created["id"]

        try:
            fetched = sf_edh.GetRecordById(
                SalesforceObjects.TARGET_SOBJECT,
                record_id,
                fields=SalesforceObjects.SELECT_FIELDS,
            )
            assert fetched.get("Id") == record_id

            updated = sf_edh.Update(
                SalesforceObjects.TARGET_SOBJECT,
                record_id,
                SalesforceObjects.UPDATE_PAYLOAD,
            )
            assert updated is True

            if SalesforceObjects.UPSERT_EXTERNAL_ID_FIELD:
                upserted = sf_edh.Upsert(
                    SalesforceObjects.TARGET_SOBJECT,
                    SalesforceObjects.UPSERT_EXTERNAL_ID_FIELD,
                    SalesforceObjects.UPSERT_EXTERNAL_ID_VALUE,
                    SalesforceObjects.UPSERT_PAYLOAD,
                )
                assert upserted.get("success") is True
                if upserted.get("id") and upserted["id"] != record_id:
                    sf_edh.deleteRecord(SalesforceObjects.TARGET_SOBJECT, upserted["id"])
        finally:
            deleted = sf_edh.deleteRecord(SalesforceObjects.TARGET_SOBJECT, record_id)
            assert deleted is True
    assert connector.is_open is False


def test_salesforce_9_bulk_read_and_10_bulk_write(salesforce_settings):
    """Test 9. BulkRead and 10. BulkWrite via Backend EDH connector."""
    if not SalesforceObjects.BULK_WRITE_RECORDS:
        pytest.skip("Configure SalesforceObjects.BULK_WRITE_RECORDS to test BulkWrite/BulkRead")

    connector = create_edh_connector(salesforce_settings, strategy="salesforce")
    with connector as sf_edh:
        assert sf_edh.is_open is True
        write_result = sf_edh.BulkWrite(
            SalesforceObjects.TARGET_SOBJECT,
            SalesforceObjects.BULK_WRITE_RECORDS,
            operation="insert",
        )
        assert write_result["state"] == "JobComplete"
        created_ids = [
            row["sf__Id"]
            for row in write_result.get("successful_results", [])
            if row.get("sf__Id")
        ]

        try:
            rows = sf_edh.BulkRead(
                SalesforceObjects.TARGET_SOBJECT,
                fields=SalesforceObjects.SELECT_FIELDS,
                where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
                limit=SalesforceObjects.QUERY_LIMIT,
            )
            assert isinstance(rows, list)
        finally:
            for cid in created_ids:
                sf_edh.deleteRecord(SalesforceObjects.TARGET_SOBJECT, cid)
    assert connector.is_open is False


def test_salesforce_async_all_operations_and_connection_lifecycle(salesforce_settings):
    """Test async Salesforce operations (createRecord, deleteRecord, describeSchema, query) via AsyncEdhConnector."""
    async_connector = create_async_edh_connector(salesforce_settings, strategy="salesforce")

    async def _run() -> None:
        assert async_connector.is_open is False
        async with async_connector as sf_edh:
            assert sf_edh.is_open is True
            assert await sf_edh.ping() is True

            schema = await sf_edh.describeSchema(SalesforceObjects.TARGET_SOBJECT)
            assert schema.get("name") == SalesforceObjects.TARGET_SOBJECT

            created = await sf_edh.createRecord(
                SalesforceObjects.TARGET_SOBJECT,
                SalesforceObjects.CREATE_PAYLOAD,
            )
            assert created.get("success") is True
            record_id = created["id"]

            try:
                fetched = await sf_edh.getRecordById(
                    SalesforceObjects.TARGET_SOBJECT,
                    record_id,
                    fields=SalesforceObjects.SELECT_FIELDS,
                )
                assert fetched.get("Id") == record_id

                assert (
                    await sf_edh.updateRecord(
                        SalesforceObjects.TARGET_SOBJECT,
                        record_id,
                        SalesforceObjects.UPDATE_PAYLOAD,
                    )
                    is True
                )
            finally:
                assert await sf_edh.deleteRecord(SalesforceObjects.TARGET_SOBJECT, record_id) is True

        assert async_connector.is_open is False

    asyncio.run(_run())
