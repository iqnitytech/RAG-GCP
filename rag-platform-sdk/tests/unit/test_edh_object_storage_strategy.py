"""Unit and contract tests for ``ObjectStorageHubStrategy`` and S3 Signature v4."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    EdhConnector,
    ObjectStorageHubStrategy,
    RawHttpResponse,
    sign_s3_v4_headers,
)
from sdk.models.edh_models import EdhConnectorConfig, compute_sha256
from tests.unit.conftest import (
    assert_blob_crud_and_pagination_contract,
    assert_partitioned_records_contract,
    assert_strategy_implements_protocol,
)


class _FakeS3HubServer:
    """In-memory S3/GCS XML Object Storage Hub HTTP simulator for contract tests."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.last_headers: dict[str, str] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawHttpResponse:
        self.last_headers = dict(headers)
        parsed = urlsplit(url)
        path = unquote(parsed.path).lstrip("/")
        parts = path.split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""

        if method == "HEAD" and not key:
            return RawHttpResponse(200, {"server": "fake-s3"})

        if method == "GET" and not key:
            qs = parse_qs(parsed.query)
            prefix = qs.get("prefix", [""])[0]
            max_keys = int(qs.get("max-keys", ["100"])[0])
            token = qs.get("continuation-token", [None])[0]

            matched = sorted(
                k for k in self.objects if k.startswith(f"{bucket}/{prefix}")
            )
            start = int(token) if token and token.isdigit() else 0
            page = matched[start : start + max_keys]
            next_tok = str(start + max_keys) if (start + max_keys) < len(matched) else None

            contents_xml = []
            for full_k in page:
                obj_key = full_k.split("/", 1)[1]
                payload, hdrs = self.objects[full_k]
                etag = hdrs.get("etag", '""')
                contents_xml.append(
                    f"<Contents><Key>{obj_key}</Key><Size>{len(payload)}</Size><ETag>{etag}</ETag></Contents>"
                )
            next_xml = f"<NextContinuationToken>{next_tok}</NextContinuationToken>" if next_tok else ""
            xml_body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<ListBucketResult>{''.join(contents_xml)}{next_xml}</ListBucketResult>"
            ).encode("utf-8")
            return RawHttpResponse(200, {"content-type": "application/xml"}, xml_body)

        full_key = f"{bucket}/{key}"
        if method == "PUT":
            if headers.get("If-None-Match") == "*" and full_key in self.objects:
                return RawHttpResponse(412, {}, b"Precondition Failed")
            data = body or b""
            sha = headers.get("x-amz-meta-sha256", compute_sha256(data))
            etag = f'"{sha[:32]}"'
            stored_headers = {
                "content-type": headers.get("Content-Type", "application/octet-stream"),
                "content-length": str(len(data)),
                "etag": etag,
                "x-amz-meta-sha256": sha,
            }
            for hk, hv in headers.items():
                if hk.lower().startswith("x-amz-meta-"):
                    stored_headers[hk.lower()] = hv
            self.objects[full_key] = (data, stored_headers)
            return RawHttpResponse(200, {"etag": etag})

        if method == "GET":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"Not Found")
            data, hdrs = self.objects[full_key]
            return RawHttpResponse(200, hdrs, data)

        if method == "HEAD":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"")
            _, hdrs = self.objects[full_key]
            return RawHttpResponse(200, hdrs, b"")

        if method == "DELETE":
            if full_key not in self.objects:
                return RawHttpResponse(404, {}, b"")
            del self.objects[full_key]
            return RawHttpResponse(204, {}, b"")

        return RawHttpResponse(400, {}, b"Unsupported")


@pytest.fixture
def object_storage_connector() -> EdhConnector:
    server = _FakeS3HubServer()
    cfg = EdhConnectorConfig(
        strategy="object_storage_hub",
        namespace="rag-enterprise",
        endpoint_url="https://s3.internal.example.com",
        access_key_getter=lambda: "AKIAIOSFODNN7EXAMPLE",
        secret_key_getter=lambda: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    return EdhConnector(cfg, strategy=ObjectStorageHubStrategy(cfg, transport=server))


def test_object_storage_strategy_implements_protocol(object_storage_connector: EdhConnector):
    assert_strategy_implements_protocol(object_storage_connector)


def test_object_storage_strategy_blob_crud_and_pagination(object_storage_connector: EdhConnector):
    assert_blob_crud_and_pagination_contract(object_storage_connector)


def test_object_storage_strategy_partitioned_records(object_storage_connector: EdhConnector):
    assert_partitioned_records_contract(object_storage_connector)


def test_s3_v4_signing():
    signed = sign_s3_v4_headers(
        method="PUT",
        url="https://s3.asia-south1.amazonaws.com/bucket/raw/doc.txt",
        headers={"Content-Type": "text/plain"},
        payload_sha256=compute_sha256(b"abc"),
        access_key="AKIA123",
        secret_key="secret456",
        region="asia-south1",
    )
    assert signed["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIA123/")
    assert "x-amz-date" in signed
