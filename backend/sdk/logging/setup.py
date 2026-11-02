"""Structured logging with request correlation ids."""

import json
import logging
import sys
from datetime import datetime, timezone

from sdk.logging.context import get_request_id


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; ``severity`` is understood by Cloud Logging."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = False,
    service: str = "app",
) -> None:
    """Configure the root logger once (idempotent).

    Uvicorn's own handlers are removed so everything flows through one
    formatter; its access log is replaced by the SDK access-log middleware.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    if json_logs:
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
            )
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False

