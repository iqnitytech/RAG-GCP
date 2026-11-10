"""Pure-ASGI middleware: request id + access log, security headers, and body size limits."""

import json
import logging
import re
import time
from typing import Mapping
from uuid import uuid4

from starlette.datastructures import MutableHeaders

from sdk.logging.context import reset_request_id, set_request_id

access_logger = logging.getLogger("sdk.access")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class _PayloadTooLargeError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


class BodySizeLimitMiddleware:
    """Enforce request body size limits before downstream parsing (e.g. multipart spooling).

    Checks both the declared ``Content-Length`` header and the actual streamed
    bytes received on the ASGI ``receive`` channel so chunked or misdeclared
    streams cannot bypass the limit.

    Args:
        app: Next ASGI app.
        default_max_bytes: Default limit for body-carrying methods when a path is
            not in ``route_limits`` (None disables the default limit).
        route_limits: Mapping of ``path`` -> ``max_bytes`` (0 or None for a route
            uses ``default_max_bytes``).
        require_content_length_paths: Optional tuple of paths that require a
            valid ``Content-Length`` header (returns 411 if missing).
    """

    BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

    def __init__(
        self,
        app,
        *,
        default_max_bytes: int | None = 11 * 1024 * 1024,
        route_limits: Mapping[str, int] | None = None,
        require_content_length_paths: tuple[str, ...] = (),
        max_bytes_message: str | None = None,
    ) -> None:
        self.app = app
        self.default_max_bytes = default_max_bytes
        self.route_limits = dict(route_limits or {})
        self.require_content_length_paths = set(require_content_length_paths)
        self.max_bytes_message = max_bytes_message

    def _limit_for_path(self, path: str) -> int | None:
        if path in self.route_limits:
            return self.route_limits[path]
        return self.default_max_bytes

    def _detail_for_limit(self, limit: int) -> str:
        if self.max_bytes_message:
            return self.max_bytes_message
        return f"Request body exceeds maximum allowed size ({limit} bytes)"

    @staticmethod
    async def _send_json_error(scope, send, status_code: int, detail: str) -> None:
        request_id = scope.get("state", {}).get("request_id", "")
        payload = {"detail": detail}
        if request_id:
            payload["request_id"] = request_id
        body = json.dumps(payload).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method not in self.BODY_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        limit = self._limit_for_path(path)

        content_length_raw: str | None = None
        for key, value in scope.get("headers", ()):
            if key == b"content-length":
                content_length_raw = value.decode("latin-1").strip()
                break

        if path in self.require_content_length_paths:
            if not content_length_raw or not content_length_raw.isdigit():
                await self._send_json_error(
                    scope, send, 411, "Content-Length header is required"
                )
                return

        if limit is not None and limit > 0 and content_length_raw:
            try:
                declared = int(content_length_raw)
            except ValueError:
                await self._send_json_error(
                    scope, send, 400, "Invalid Content-Length header"
                )
                return
            if declared > limit:
                await self._send_json_error(
                    scope, send, 413, self._detail_for_limit(limit)
                )
                return

        if limit is None or limit <= 0:
            await self.app(scope, receive, send)
            return

        received_bytes = 0
        response_started = False

        async def guarded_receive():
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > limit:
                    raise _PayloadTooLargeError(413, self._detail_for_limit(limit))
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, tracked_send)
        except _PayloadTooLargeError as exc:
            if not response_started:
                await self._send_json_error(scope, send, exc.status_code, exc.detail)
                return
            raise



class RequestContextMiddleware:
    """Assign/propagate ``X-Request-ID`` and emit one access-log line per request."""

    def __init__(self, app, quiet_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.quiet_paths = quiet_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = ""
        for key, value in scope["headers"]:
            if key == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid4().hex

        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in self.quiet_paths:
                access_logger.info(
                    "%s %s -> %d (%.1f ms)",
                    scope.get("method", "-"),
                    path,
                    status,
                    (time.perf_counter() - started) * 1000,
                )
            reset_request_id(token)


class SecurityHeadersMiddleware:
    """Add conservative security headers to every HTTP response."""

    def __init__(self, app, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=63072000; includeSubDomains",
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)

