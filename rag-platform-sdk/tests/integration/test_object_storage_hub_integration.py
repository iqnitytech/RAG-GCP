"""Live HTTP integration tests for ``ObjectStorageHubStrategy`` with AWS SigV4 signing."""

from __future__ import annotations

from sdk.connectors.edh import (
    DataZone,
    EdhConnector,
    EdhConnectorConfig,
    EdhWriteOptions,
)


def test_integration_object_storage_hub_over_live_http_with_sigv4(live_hub):
    base_url, state = live_hub
    cfg = EdhConnectorConfig(
        strategy="object_storage_hub",
        namespace="s3-rag-bucket",
        endpoint_url=base_url,
        region="asia-south1",
        access_key_getter=lambda: "AKIAINTEGRATIONTEST",
        secret_key_getter=lambda: "supersecretintegrationkey",
    )

    with EdhConnector(cfg) as edh:
        assert edh.ping() is True

        data = b"Object Storage Hub live HTTP + SigV4 test"
        meta = edh.put_bytes(
            "manuals/install_guide.txt",
            data,
            zone=DataZone.RAW,
            options=EdhWriteOptions(
                content_type="text/plain",
                custom_metadata={"dept": "eng"},
            ),
        )
        assert meta.size_bytes == len(data)
        last_auth = state.request_headers_log[-1].get("authorization", "")
        assert last_auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAINTEGRATIONTEST/")

        assert edh.get_bytes("manuals/install_guide.txt", zone=DataZone.RAW) == data
        head = edh.head("manuals/install_guide.txt", zone=DataZone.RAW)
        assert head.size_bytes == len(data)
        assert head.custom_metadata.get("dept") == "eng"

        page = edh.list_objects(zone=DataZone.RAW, prefix="manuals")
        assert len(page.items) == 1
        assert page.items[0].ref.key == "manuals/install_guide.txt"
