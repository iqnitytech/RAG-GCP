"""Shared fixtures and ``.env.integration`` loader for SDK integration tests."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from sdk.connectors.edh import compute_sha256

INTEGRATION_ENV_FILE = Path(__file__).resolve().parent / ".env.integration"


def load_integration_env(env_path: Path = INTEGRATION_ENV_FILE) -> dict[str, str]:
    """Load key=value pairs from ``tests/integration/.env.integration`` merged with ``os.environ``."""
    values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip("'\"")
    for key, val in os.environ.items():
        if val:
            values[key] = val
    return values


class _LiveHubState:
    def __init__(self) -> None:
        self.s3_objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.api_objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.request_headers_log: list[dict[str, str]] = []
        self.fail_next_get_count: int = 0


def _make_handler(state: _LiveHubState):
    class LiveHubHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _record_headers(self) -> dict[str, str]:
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            state.request_headers_log.append(hdrs)
            return hdrs

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length > 0 else b""

        def do_HEAD(self) -> None:
            self._record_headers()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)

            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                full_key = f"{parts[2]}/{parts[4]}/{'/'.join(parts[6:])}"
                if full_key not in state.api_objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                _, meta = state.api_objects[full_key]
                self.send_response(200)
                for k, v in meta.items():
                    self.send_header(k, v)
                self.end_headers()
                return

            stripped = path.lstrip("/")
            parts = stripped.split("/", 1)
            if len(parts) == 1:
                self.send_response(200)
                self.end_headers()
                return
            if stripped not in state.s3_objects:
                self.send_response(404)
                self.end_headers()
                return
            _, meta = state.s3_objects[stripped]
            self.send_response(200)
            for k, v in meta.items():
                self.send_header(k, v)
            self.end_headers()

        def do_PUT(self) -> None:
            hdrs = self._record_headers()
            body = self._read_body()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)

            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                full_key = f"{parts[2]}/{parts[4]}/{'/'.join(parts[6:])}"
                if hdrs.get("x-edh-overwrite") == "false" and full_key in state.api_objects:
                    self.send_response(409)
                    self.end_headers()
                    return
                sha = hdrs.get("x-edh-sha256", compute_sha256(body))
                etag = f'"{sha[:32]}"'
                meta = {
                    "Content-Type": hdrs.get("content-type", "application/octet-stream"),
                    "Content-Length": str(len(body)),
                    "X-EDH-SHA256": sha,
                    "ETag": etag,
                }
                for hk, hv in hdrs.items():
                    if hk.startswith("x-edh-meta-"):
                        meta[hk] = hv
                state.api_objects[full_key] = (body, meta)
                resp_bytes = json.dumps({"etag": etag, "version": sha[:16]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.send_header("ETag", etag)
                self.end_headers()
                self.wfile.write(resp_bytes)
                return

            full_key = path.lstrip("/")
            if hdrs.get("if-none-match") == "*" and full_key in state.s3_objects:
                self.send_response(412)
                self.end_headers()
                return
            sha = hdrs.get("x-amz-meta-sha256", compute_sha256(body))
            etag = f'"{sha[:32]}"'
            meta = {
                "Content-Type": hdrs.get("content-type", "application/octet-stream"),
                "Content-Length": str(len(body)),
                "ETag": etag,
                "x-amz-meta-sha256": sha,
            }
            for hk, hv in hdrs.items():
                if hk.startswith("x-amz-meta-"):
                    meta[hk] = hv
            state.s3_objects[full_key] = (body, meta)
            self.send_response(200)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            hdrs = self._record_headers()
            body = self._read_body()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            qs = parse_qs(parsed.query)

            parts = path.strip("/").split("/")
            ns, zone = parts[2], parts[4]
            dataset = "/".join(parts[6:-1])
            part_segs = [
                f"{k[len('partition.'):]}={v[0]}"
                for k, v in sorted(qs.items())
                if k.startswith("partition.")
            ]
            sha = hdrs.get("x-edh-sha256", compute_sha256(body))
            key = "/".join([dataset, *part_segs, f"part-{sha[:16]}.jsonl"])
            full_key = f"{ns}/{zone}/{key}"
            state.api_objects[full_key] = (
                body,
                {
                    "Content-Type": hdrs.get("content-type", "application/x-ndjson"),
                    "Content-Length": str(len(body)),
                    "X-EDH-SHA256": sha,
                    "ETag": f'"{sha[:32]}"',
                },
            )
            resp_bytes = json.dumps({"key": key, "etag": f'"{sha[:32]}"'}).encode("utf-8")
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        def do_GET(self) -> None:
            self._record_headers()
            if state.fail_next_get_count > 0:
                state.fail_next_get_count -= 1
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            qs = parse_qs(parsed.query)

            if path == "/health":
                payload = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                ns, zone, kind = parts[2], parts[4], parts[5]

                if kind == "datasets":
                    dataset = "/".join(parts[6:-1])
                    part_segs = [
                        f"{k[len('partition.'):]}={v[0]}"
                        for k, v in sorted(qs.items())
                        if k.startswith("partition.")
                    ]
                    prefix = f"{ns}/{zone}/{dataset}"
                    out = b"".join(
                        payload
                        for fk, (payload, _) in sorted(state.api_objects.items())
                        if fk.startswith(prefix) and all(ps in set(fk.split("/")) for ps in part_segs)
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return

                if kind == "objects" and len(parts) == 6:
                    prefix = qs.get("prefix", [""])[0]
                    base = f"{ns}/{zone}/"
                    items = [
                        {
                            "key": fk[len(base) :],
                            "size_bytes": len(p),
                            "content_type": m.get("Content-Type", "application/octet-stream"),
                            "sha256": m.get("X-EDH-SHA256", ""),
                            "etag": m.get("ETag", ""),
                        }
                        for fk, (p, m) in sorted(state.api_objects.items())
                        if fk.startswith(f"{base}{prefix}")
                    ]
                    out = json.dumps({"items": items, "next_page_token": None}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return

                full_key = f"{ns}/{zone}/{'/'.join(parts[6:])}"
                if full_key not in state.api_objects:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload, meta = state.api_objects[full_key]
                self.send_response(200)
                for k, v in meta.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)
                return

            stripped = path.lstrip("/")
            parts = stripped.split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""

            if not key:
                prefix = qs.get("prefix", [""])[0]
                matched = sorted(
                    k for k in state.s3_objects if k.startswith(f"{bucket}/{prefix}")
                )
                contents = []
                for fk in matched:
                    obj_key = fk.split("/", 1)[1]
                    payload, meta = state.s3_objects[fk]
                    etag = meta.get("ETag", '""')
                    contents.append(
                        f"<Contents><Key>{obj_key}</Key><Size>{len(payload)}</Size><ETag>{etag}</ETag></Contents>"
                    )
                xml_bytes = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f"<ListBucketResult>{''.join(contents)}</ListBucketResult>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(xml_bytes)))
                self.end_headers()
                self.wfile.write(xml_bytes)
                return

            if stripped not in state.s3_objects:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload, meta = state.s3_objects[stripped]
            self.send_response(200)
            for k, v in meta.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

        def do_DELETE(self) -> None:
            self._record_headers()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path.startswith("/v1/namespaces/"):
                parts = path.strip("/").split("/")
                full_key = f"{parts[2]}/{parts[4]}/{'/'.join(parts[6:])}"
                if full_key not in state.api_objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                del state.api_objects[full_key]
                self.send_response(204)
                self.end_headers()
                return

            full_key = path.lstrip("/")
            if full_key not in state.s3_objects:
                self.send_response(404)
                self.end_headers()
                return
            del state.s3_objects[full_key]
            self.send_response(204)
            self.end_headers()

    return LiveHubHandler


@pytest.fixture
def live_hub():
    """Spin up a real loopback ThreadingHTTPServer for HTTP integration tests."""
    state = _LiveHubState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
