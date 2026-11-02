"""Pure-ASGI middleware: request id + access log, and security headers."""

import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders

from sdk.logging.context import reset_request_id, set_request_id

access_logger = logging.getLogger("sdk.access")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


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

