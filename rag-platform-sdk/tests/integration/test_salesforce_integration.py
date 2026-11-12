"""Real-endpoint integration tests for ``SalesforceEdhStrategy``.

Configure credentials in ``rag-platform-sdk/tests/integration/.env.integration``,
Salesforce object names/payloads in ``rag-platform-sdk/tests/integration/salesforce_objects.py``,
and generic constants in ``rag-platform-sdk/tests/integration/constants.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from sdk.connectors.edh import AsyncEdhConnector, DataZone, EdhConnector
from tests.integration.constants import DEFAULT_PAGE_SIZE
from tests.integration.salesforce_objects import SalesforceObjects


def test_salesforce_ping_and_fetch_sobject_records(salesforce_connector: EdhConnector):
    """Connect to the configured Salesforce org and query SObject records from SalesforceObjects."""
    assert salesforce_connector.is_open is False
    with salesforce_connector as sf:
        assert sf.is_open is True
        assert sf.ping() is True
        records = list(
            sf.read_records(
                SalesforceObjects.TARGET_SOBJECT,
                zone=DataZone.CURATED,
                partition_filter=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
            )
        )
        assert isinstance(records, list)
    assert salesforce_connector.is_open is False


def test_salesforce_list_content_version_objects(salesforce_connector: EdhConnector):
    """List ContentVersion documents from the configured Salesforce org."""
    with salesforce_connector as sf:
        assert sf.is_open is True
        page = sf.list_objects(
            zone=DataZone.RAW,
            prefix=SalesforceObjects.CONTENT_VERSION_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(page.items, tuple)
    assert salesforce_connector.is_open is False


def test_salesforce_1_describe_schema_2_query_3_paginated_query(salesforce_connector: EdhConnector):
    """Test 1. DescribeSchema, 2. Query, and 3. PaginatedQuery against the live Salesforce org."""
    with salesforce_connector as sf:
        assert sf.is_open is True
        # 1. DescribeSchema
        schema = sf.DescribeSchema(SalesforceObjects.TARGET_SOBJECT)
        assert schema.get("name") == SalesforceObjects.TARGET_SOBJECT
        assert isinstance(schema.get("fields"), list)

        # 2. Query
        records = sf.Query(
            SalesforceObjects.TARGET_SOBJECT,
            fields=SalesforceObjects.SELECT_FIELDS,
            where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
            limit=SalesforceObjects.QUERY_LIMIT,
        )
        assert isinstance(records, list)

        # 3. PaginatedQuery
        pages = list(
            sf.PaginatedQuery(
                SalesforceObjects.TARGET_SOBJECT,
                fields=SalesforceObjects.SELECT_FIELDS,
                where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
                limit=SalesforceObjects.QUERY_LIMIT,
                batch_size=SalesforceObjects.PAGINATED_BATCH_SIZE,
            )
        )
        assert isinstance(pages, list)
    assert salesforce_connector.is_open is False


def test_salesforce_4_get_5_create_record_6_update_7_upsert_8_delete_record(
    salesforce_connector: EdhConnector,
):
    """Test 4. GetRecordById, 5. createRecord, 6. Update, 7. Upsert, and 8. deleteRecord against Salesforce."""
    with salesforce_connector as sf:
        assert sf.is_open is True
        # 5. createRecord / Create
        created = sf.createRecord(
            SalesforceObjects.TARGET_SOBJECT,
            SalesforceObjects.CREATE_PAYLOAD,
        )
        assert created.get("success") is True
        record_id = created["id"]

        try:
            # 4. GetRecordById
            fetched = sf.GetRecordById(
                SalesforceObjects.TARGET_SOBJECT,
                record_id,
                fields=SalesforceObjects.SELECT_FIELDS,
            )
            assert fetched.get("Id") == record_id

            # 6. Update
            updated = sf.Update(
                SalesforceObjects.TARGET_SOBJECT,
                record_id,
                SalesforceObjects.UPDATE_PAYLOAD,
            )
            assert updated is True

            # 7. Upsert (runs when UPSERT_EXTERNAL_ID_FIELD is configured in SalesforceObjects)
            if SalesforceObjects.UPSERT_EXTERNAL_ID_FIELD:
                upserted = sf.Upsert(
                    SalesforceObjects.TARGET_SOBJECT,
                    SalesforceObjects.UPSERT_EXTERNAL_ID_FIELD,
                    SalesforceObjects.UPSERT_EXTERNAL_ID_VALUE,
                    SalesforceObjects.UPSERT_PAYLOAD,
                )
                assert upserted.get("success") is True
                if upserted.get("id") and upserted["id"] != record_id:
                    sf.deleteRecord(SalesforceObjects.TARGET_SOBJECT, upserted["id"])
        finally:
            # 8. deleteRecord / Delete
            deleted = sf.deleteRecord(SalesforceObjects.TARGET_SOBJECT, record_id)
            assert deleted is True
    assert salesforce_connector.is_open is False


def test_salesforce_9_bulk_read_and_10_bulk_write(salesforce_connector: EdhConnector):
    """Test 9. BulkRead and 10. BulkWrite (Bulk API 2.0) against the live Salesforce org."""
    if not SalesforceObjects.BULK_WRITE_RECORDS:
        pytest.skip("Configure SalesforceObjects.BULK_WRITE_RECORDS to test BulkWrite/BulkRead")

    with salesforce_connector as sf:
        assert sf.is_open is True
        # 10. BulkWrite
        write_result = sf.BulkWrite(
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
            # 9. BulkRead
            rows = sf.BulkRead(
                SalesforceObjects.TARGET_SOBJECT,
                fields=SalesforceObjects.SELECT_FIELDS,
                where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
                limit=SalesforceObjects.QUERY_LIMIT,
            )
            assert isinstance(rows, list)
        finally:
            for cid in created_ids:
                sf.deleteRecord(SalesforceObjects.TARGET_SOBJECT, cid)
    assert salesforce_connector.is_open is False


def test_salesforce_async_all_operations_and_connection_lifecycle(
    async_salesforce_connector: AsyncEdhConnector,
):
    """Test async Salesforce operations (createRecord, deleteRecord, describeSchema, query, etc.) and lifecycle."""

    async def _run() -> None:
        assert async_salesforce_connector.is_open is False
        async with async_salesforce_connector as sf:
            assert sf.is_open is True
            assert await sf.ping() is True

            schema = await sf.describeSchema(SalesforceObjects.TARGET_SOBJECT)
            assert schema.get("name") == SalesforceObjects.TARGET_SOBJECT

            created = await sf.createRecord(
                SalesforceObjects.TARGET_SOBJECT,
                SalesforceObjects.CREATE_PAYLOAD,
            )
            assert created.get("success") is True
            record_id = created["id"]

            try:
                fetched = await sf.getRecordById(
                    SalesforceObjects.TARGET_SOBJECT,
                    record_id,
                    fields=SalesforceObjects.SELECT_FIELDS,
                )
                assert fetched.get("Id") == record_id

                assert (
                    await sf.updateRecord(
                        SalesforceObjects.TARGET_SOBJECT,
                        record_id,
                        SalesforceObjects.UPDATE_PAYLOAD,
                    )
                    is True
                )

                queried = await sf.query(
                    SalesforceObjects.TARGET_SOBJECT,
                    fields=SalesforceObjects.SELECT_FIELDS,
                    where=SalesforceObjects.TARGET_SOBJECT_FILTER or None,
                    limit=SalesforceObjects.QUERY_LIMIT,
                )
                assert isinstance(queried, list)
            finally:
                assert await sf.deleteRecord(SalesforceObjects.TARGET_SOBJECT, record_id) is True

        assert async_salesforce_connector.is_open is False

    asyncio.run(_run())
