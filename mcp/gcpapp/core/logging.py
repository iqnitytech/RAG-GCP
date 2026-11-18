from __future__ import annotations
import logging
import sys
from typing import Any
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

# CODE-03: Use log_format instead of format to avoid shadowing Python's built-in format()
def configure_logging(level: str = "INFO", log_format: str = "JSON", **kwargs: Any) -> None:
    fmt = kwargs.get("format", log_format)
    log_level = getattr(logging, level.upper(), logging.INFO)
    if fmt.upper() == "JSON":
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(stream=sys.stderr, level=log_level)

def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)

def bind_context(**kwargs: object) -> None:
    bind_contextvars(**kwargs)

def clear_context() -> None:
    clear_contextvars()
