"""Real-endpoint integration tests for ``ObjectStorageHubStrategy``.

Configure ``EDH_S3_ENDPOINT_URL``, ``EDH_S3_ACCESS_KEY``, and ``EDH_S3_SECRET_KEY``
in ``rag-platform-sdk/tests/integration/.env.integration`` and object constants in
``rag-platform-sdk/tests/integration/constants.py``.
"""

from __future__ import annotations

from sdk.connectors.edh import DataZone, EdhConnector, EdhWriteOptions
from tests.integration.constants import (
    DEFAULT_PAGE_SIZE,
    OBJECT_STORAGE_BYTES,
    OBJECT_STORAGE_CUSTOM_METADATA,
    OBJECT_STORAGE_OBJECT_KEY,
    OBJECT_STORAGE_PREFIX,
)


def test_object_storage_hub_put_get_head_and_list(object_storage_connector: EdhConnector):
    with object_storage_connector as edh:
        assert edh.ping() is True

        meta = edh.put_bytes(
            OBJECT_STORAGE_OBJECT_KEY,
            OBJECT_STORAGE_BYTES,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type="text/plain",
                custom_metadata=OBJECT_STORAGE_CUSTOM_METADATA,
            ),
        )
        assert meta.size_bytes == len(OBJECT_STORAGE_BYTES)
        assert edh.get_bytes(OBJECT_STORAGE_OBJECT_KEY, zone=DataZone.RAW) == OBJECT_STORAGE_BYTES

        head = edh.head(OBJECT_STORAGE_OBJECT_KEY, zone=DataZone.RAW)
        assert head.size_bytes == len(OBJECT_STORAGE_BYTES)

        page = edh.list_objects(
            zone=DataZone.RAW,
            prefix=OBJECT_STORAGE_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert len(page.items) >= 1
