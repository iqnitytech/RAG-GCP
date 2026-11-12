"""Pure Python standard-library HTTP transport for EDH strategies.

Uses ``urllib.request``, ``urllib.error``, ``ssl``, ``hmac``, and ``hashlib``
so strategies can talk to Object Storage Hubs (S3/GCS XML/JSON endpoints) and
API-driven Data Hubs without requiring third-party HTTP libraries at runtime,
while also allowing a custom transport callable to be injected for unit and
contract testing.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Mapping
from urllib.parse import quote, urlsplit

from sdk.connectors.edh.errors import (
    EdhAuthError,
    EdhConflictError,
    EdhError,
    EdhNotFoundError,
    EdhRateLimitError,
    EdhTransientError,
    log_and_build_edh_error,
)

http_logger = logging.getLogger("sdk.edh.http")


@dataclass(frozen=True)
class RawHttpResponse:
    """Low-level HTTP response returned by a :data:`TransportCallable`."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str, default: str = "") -> str:
        target = name.lower()
        for k, v in self.headers.items():
            if k.lower() == target:
                return v
        return default


TransportCallable = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    RawHttpResponse,
]


def urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: float,
    *,
    verify_tls: bool = True,
) -> RawHttpResponse:
    """Execute an HTTP request using Python's built-in ``urllib.request``."""
    req = urllib.request.Request(
        url=url,
        data=body,
        headers=dict(headers),
        method=method.upper(),
    )
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds, context=context) as resp:  # noqa: S310
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            payload = resp.read() if method.upper() != "HEAD" else b""
            return RawHttpResponse(
                status_code=int(resp.status),
                headers=resp_headers,
                body=payload,
            )
    except urllib.error.HTTPError as exc:
        resp_headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else ())}
        err_body = exc.read() if exc.fp is not None and method.upper() != "HEAD" else b""
        return RawHttpResponse(
            status_code=int(exc.code),
            headers=resp_headers,
            body=err_body,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise log_and_build_edh_error(
            http_logger,
            EdhTransientError,
            f"Network/timeout error on {method} {url}: {exc}",
            provider="http_transport",
            operation=f"{method} {url}",
            error_code="EDH_HTTP_NETWORK_TIMEOUT",
        ) from exc


class HttpConnectionPool:
    """Thread-safe HTTP/HTTPS connection manager built on ``http.client``.

    Properly opens and reuses persistent connections while a session is open,
    and closes all underlying sockets when :meth:`close` is called (or immediately
    after a one-off request when called outside an explicit ``open()`` session).
    """

    def __init__(self, *, verify_tls: bool = True) -> None:
        self._verify_tls = verify_tls
        self._lock = Lock()
        self._is_open = False
        self._pool: dict[tuple[str, str, int, float], list[http.client.HTTPConnection]] = {}
        self.open_count = 0
        self.close_count = 0

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._is_open

    @property
    def active_connections(self) -> int:
        with self._lock:
            return sum(len(conns) for conns in self._pool.values())

    def open(self) -> HttpConnectionPool:
        """Open the connection pool session for persistent socket reuse."""
        with self._lock:
            if not self._is_open:
                self._is_open = True
                self.open_count += 1
        return self

    def close(self) -> None:
        """Close all pooled HTTP/HTTPS connections and mark the session closed."""
        with self._lock:
            conns_to_close = [c for bucket in self._pool.values() for c in bucket]
            self._pool.clear()
            if self._is_open or conns_to_close:
                self.close_count += 1
            self._is_open = False

        for conn in conns_to_close:
            try:
                conn.close()
            except Exception:
                pass

    def _create_connection(
        self,
        scheme: str,
        host: str,
        port: int,
        timeout_seconds: float,
    ) -> http.client.HTTPConnection:
        if scheme == "https":
            context = ssl.create_default_context()
            if not self._verify_tls:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection(
                host,
                port,
                timeout=timeout_seconds,
                context=context,
            )
        return http.client.HTTPConnection(host, port, timeout=timeout_seconds)

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> RawHttpResponse:
        """Execute an HTTP request, reusing pooled connections when open and closing when done."""
        parsed = urlsplit(url)
        scheme = (parsed.scheme or "https").lower()
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if scheme == "https" else 80)
        target_path = parsed.path or "/"
        if parsed.query:
            target_path = f"{target_path}?{parsed.query}"

        key = (scheme, host, port, float(timeout_seconds))
        with self._lock:
            keep_open = self._is_open
            bucket = self._pool.get(key)
            conn = bucket.pop() if bucket else None

        if conn is None:
            conn = self._create_connection(scheme, host, port, timeout_seconds)

        req_headers = dict(headers)
        req_headers.setdefault("Connection", "keep-alive" if keep_open else "close")

        try:
            try:
                conn.request(method.upper(), target_path, body=body, headers=req_headers)
                resp = conn.getresponse()
            except (http.client.CannotSendRequest, http.client.BadStatusLine, ConnectionError):
                # Reconnect once if a pooled keep-alive socket was closed by peer
                try:
                    conn.close()
                except Exception:
                    pass
                conn = self._create_connection(scheme, host, port, timeout_seconds)
                conn.request(method.upper(), target_path, body=body, headers=req_headers)
                resp = conn.getresponse()

            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            payload = resp.read() if method.upper() != "HEAD" else b""
            raw_resp = RawHttpResponse(
                status_code=int(resp.status),
                headers=resp_headers,
                body=payload,
            )

            with self._lock:
                still_open = self._is_open
                if still_open and keep_open and resp_headers.get("connection", "").lower() != "close":
                    self._pool.setdefault(key, []).append(conn)
                    conn = None
            return raw_resp
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            raise log_and_build_edh_error(
                http_logger,
                EdhTransientError,
                f"Network/timeout error on {method} {url}: {exc}",
                provider="http_pool",
                operation=f"{method} {url}",
                error_code="EDH_HTTP_NETWORK_TIMEOUT",
            ) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def raise_for_status(
    response: RawHttpResponse,
    *,
    provider: str,
    operation: str,
) -> None:
    """Translate HTTP status codes into the typed :class:`EdhError` taxonomy."""
    code = response.status_code
    if 200 <= code < 300:
        return
    if code in (401, 403):
        raise EdhAuthError(
            f"{provider} {operation} failed with HTTP {code}",
            provider=provider,
            operation=operation,
        )
    if code == 404:
        raise EdhNotFoundError(
            f"{provider} {operation} returned 404 Not Found",
            provider=provider,
            operation=operation,
        )
    if code in (409, 412):
        raise EdhConflictError(
            f"{provider} {operation} returned HTTP {code} Conflict",
            provider=provider,
            operation=operation,
        )
    if code == 429:
        retry_raw = response.header("retry-after")
        retry_after = float(retry_raw) if retry_raw.isdigit() else None
        raise EdhRateLimitError(
            f"{provider} {operation} rate limited (HTTP 429)",
            retry_after_seconds=retry_after,
            provider=provider,
            operation=operation,
        )
    if code >= 500 or code == 408:
        raise EdhTransientError(
            f"{provider} {operation} returned transient HTTP {code}",
            provider=provider,
            operation=operation,
        )
    raise EdhError(
        f"{provider} {operation} returned unexpected HTTP {code}",
        provider=provider,
        operation=operation,
        retryable=False,
    )


def sign_s3_v4_headers(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload_sha256: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str = "s3",
    now: datetime | None = None,
) -> dict[str, str]:
    """Compute AWS Signature Version 4 headers using Python core ``hmac`` & ``hashlib``."""
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    amz_date = ts.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = ts.strftime("%Y%m%d")

    parsed = urlsplit(url)
    host = parsed.netloc
    canonical_uri = quote(parsed.path or "/", safe="/-_.~")

    # Canonical query string sorted by parameter name
    query_pairs: list[tuple[str, str]] = []
    if parsed.query:
        for part in parsed.query.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                query_pairs.append((quote(k, safe="-_.~"), quote(v, safe="-_.~")))
            elif part:
                query_pairs.append((quote(part, safe="-_.~"), ""))
    query_pairs.sort()
    canonical_querystring = "&".join(f"{k}={v}" for k, v in query_pairs)

    merged: dict[str, str] = {k.lower(): v.strip() for k, v in headers.items()}
    merged["host"] = host
    merged["x-amz-date"] = amz_date
    merged["x-amz-content-sha256"] = payload_sha256

    sorted_header_keys = sorted(merged.keys())
    canonical_headers = "".join(f"{k}:{merged[k]}\n" for k in sorted_header_keys)
    signed_headers = ";".join(sorted_header_keys)

    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            payload_sha256,
        ]
    )

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    out = dict(headers)
    out["Host"] = host
    out["x-amz-date"] = amz_date
    out["x-amz-content-sha256"] = payload_sha256
    out["Authorization"] = authorization
    return out
