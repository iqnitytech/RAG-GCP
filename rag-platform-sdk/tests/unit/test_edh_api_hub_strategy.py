"""Unit and contract tests for ``ApiDrivenEdhStrategy``."""

from __future__ import annotations

import json
from typing import Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import (
    ApiDrivenEdhStrategy,
    EdhAuthError,
    EdhConnector,
    EdhRateLimitError,
    RawHttpResponse,
)
from sdk.logging.context import reset_request_id, set_request_id
from sdk.models.edh_models import EdhConnectorConfig, RetryPolicy, compute_sha256
from tests.unit.conftest import (
    assert_blob_crud_and_pagination_contract,
    assert_partitioned_records_contract,
    assert_strategy_implements_protocol,
)


class _FakeApiHubServer:
    """In-memory REST API Data Hub simulator for contract tests."""

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
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path == "/health":
            return RawHttpResponse(200, {"content-type": "application/json"}, b'{"status":"ok"}')

        parts = path.strip("/").split("/")
        ns = parts[2]
        zone = parts[4]
        kind = parts[5]

        if kind == "datasets":
            ds_parts = parts[6:-1]
            dataset = "/".join(ds_parts)
            part_segs = [
                f"{k[len('partition.'):]}={v[0]}"
                for k, v in sorted(qs.items())
                if k.startswith("partition.")
            ]
            if method == "POST":
                data = body or b""
                sha = headers.get("X-EDH-SHA256", compute_sha256(data))
                key = "/".join([dataset, *part_segs, f"part-{sha[:16]}.jsonl"])
                full_key = f"{ns}/{zone}/{key}"
                self.objects[full_key] = (
                    data,
                    {
                        "content-type": headers.get("Content-Type", "application/x-ndjson"),
                        "content-length": str(len(data)),
                        "x-edh-sha256": sha,
                        "etag": f'"{sha[:32]}"',
                    },
                )
                return RawHttpResponse(
                    201,
                    {"content-type": "application/json"},
                    json.dumps({"key": key, "etag": f'"{sha[:32]}"'}).encode("utf-8"),
                )
            if method == "GET":
                prefix = f"{ns}/{zone}/{dataset}"
                chunks: list[bytes] = []
                for fk, (payload, _) in sorted(self.objects.items()):
                    if not fk.startswith(prefix):
                        continue
                    segs = set(fk.split("/"))
                    if any(ps not in segs for ps in part_segs):
                        continue
                    chunks.append(payload)
                return RawHttpResponse(200, {"content-type": "application/x-ndjson"}, b"".join(chunks))

        if kind == "objects" and len(parts) == 6:
            prefix = qs.get("prefix", [""])[0]
            page_size = int(qs.get("page_size", ["100"])[0])
            page_token = qs.get("page_token", [None])[0]
            base_prefix = f"{ns}/{zone}/"
            full_prefix = f"{base_prefix}{prefix}"

            matched = [
                (fk[len(base_prefix) :], payload, hdrs)
                for fk, (payload, hdrs) in sorted(self.objects.items())
                if fk.startswith(full_prefix)
            ]
            start = int(page_token) if page_token and page_token.isdigit() else 0
            sliced = matched[start : start + page_size]
            next_tok = str(start + page_size) if (start + page_size) < len(matched) else None
            doc = {
                "items": [
                    {
                        "key": k,
                        "size_bytes": len(p),
                        "content_type": h.get("content-type", "application/octet-stream"),
                        "sha256": h.get("x-edh-sha256", ""),
                        "etag": h.get("etag", ""),
                    }
                    for k, p, h in sliced
                ],
                "next_page_token": next_tok,
            }
            return RawHttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(doc).encode("utf-8"),
            )

        key = "/".join(parts[6:])
        full_key = f"{ns}/{zone}/{key}"

        if method == "PUT":
            if headers.get("X-EDH-Overwrite") == "false" and full_key in self.objects:
                return RawHttpResponse(409, {}, b"Conflict")
            data = body or b""
            sha = headers.get("X-EDH-SHA256", compute_sha256(data))
            etag = f'"{sha[:32]}"'
            hdrs = {
                "content-type": headers.get("Content-Type", "application/octet-stream"),
                "content-length": str(len(data)),
                "x-edh-sha256": sha,
                "etag": etag,
            }
            for hk, hv in headers.items():
                if hk.lower().startswith("x-edh-meta-"):
                    hdrs[hk.lower()] = hv
            self.objects[full_key] = (data, hdrs)
            return RawHttpResponse(
                200,
                {"content-type": "application/json", "etag": etag},
                json.dumps({"etag": etag, "version": sha[:16]}).encode("utf-8"),
            )

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

        return RawHttpResponse(400, {}, b"Bad Request")


@pytest.fixture
def api_hub_connector() -> EdhConnector:
    server = _FakeApiHubServer()
    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="rag-enterprise",
        endpoint_url="https://edh-api.internal.example.com",
        auth_token_getter=lambda: "eyJhbGciOiJSUzI1NiJ9.token",
    )
    return EdhConnector(cfg, strategy=ApiDrivenEdhStrategy(cfg, transport=server))


def test_api_hub_strategy_implements_protocol(api_hub_connector: EdhConnector):
    assert_strategy_implements_protocol(api_hub_connector)


def test_api_hub_strategy_blob_crud_and_pagination(api_hub_connector: EdhConnector):
    assert_blob_crud_and_pagination_contract(api_hub_connector)


def test_api_hub_strategy_partitioned_records(api_hub_connector: EdhConnector):
    assert_partitioned_records_contract(api_hub_connector)


def test_api_hub_propagates_request_id_and_maps_auth_and_rate_limit():
    captured_headers: dict[str, str] = {}
    status_to_return = 401

    def transport(method, url, headers, body, timeout) -> RawHttpResponse:
        captured_headers.update(headers)
        return RawHttpResponse(status_to_return, {"retry-after": "2"}, b"err")

    cfg = EdhConnectorConfig(
        strategy="api_hub",
        namespace="ns",
        endpoint_url="https://edh.example.com",
        auth_token_getter=lambda: "tok-123",
        retry_policy=RetryPolicy(max_attempts=1),
    )
    conn = EdhConnector(cfg, strategy=ApiDrivenEdhStrategy(cfg, transport=transport))

    token = set_request_id("req-corr-999")
    try:
        with pytest.raises(EdhAuthError):
            conn.get_bytes("doc.txt")
        assert captured_headers["X-Request-ID"] == "req-corr-999"
        assert captured_headers["Authorization"] == "Bearer tok-123"

        status_to_return = 429
        with pytest.raises(EdhRateLimitError) as exc_info:
            conn.get_bytes("doc.txt")
        assert exc_info.value.retry_after_seconds == 2.0
    finally:
        reset_request_id(token)
