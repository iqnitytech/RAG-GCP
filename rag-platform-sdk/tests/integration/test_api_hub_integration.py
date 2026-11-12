"""Real-endpoint integration tests for ``ApiDrivenEdhStrategy``.

Configure ``EDH_API_HUB_URL`` and ``EDH_API_HUB_TOKEN`` / ``EDH_API_HUB_KEY``
in ``rag-platform-sdk/tests/integration/.env.integration`` and object constants in
``rag-platform-sdk/tests/integration/constants.py``.
"""

from __future__ import annotations

from sdk.connectors.edh import DataZone, EdhConnector, EdhWriteOptions, compute_sha256
from tests.integration.constants import (
    API_HUB_BYTES,
    API_HUB_DATASET,
    API_HUB_OBJECT_KEY,
    API_HUB_PARTITION_FILTER,
    API_HUB_PREFIX,
    DEFAULT_PAGE_SIZE,
)


def test_api_hub_put_get_list_and_read_records(api_hub_connector: EdhConnector):
    with api_hub_connector as edh:
        assert edh.ping() is True

        meta = edh.put_bytes(
            API_HUB_OBJECT_KEY,
            API_HUB_BYTES,
            zone=DataZone.RAW,
            options=EdhWriteOptions(content_type="application/pdf"),
        )
        assert meta.sha256 == compute_sha256(API_HUB_BYTES)
        assert edh.get_bytes(API_HUB_OBJECT_KEY, zone=DataZone.RAW) == API_HUB_BYTES

        page = edh.list_objects(
            zone=DataZone.RAW,
            prefix=API_HUB_PREFIX,
            page_size=DEFAULT_PAGE_SIZE,
        )
        assert isinstance(page.items, tuple)

        rows = list(
            edh.read_records(
                API_HUB_DATASET,
                zone=DataZone.CURATED,
                partition_filter=API_HUB_PARTITION_FILTER or None,
            )
        )
        assert isinstance(rows, list)
