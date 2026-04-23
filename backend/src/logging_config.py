"""Centralized logging configuration for the mini-rag backend."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

from .config import Settings

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")

LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "[request_id=%(request_id)s] %(message)s"
)
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class RequestIdFilter(logging.Filter):
    """Attach the current request ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add request_id to the log record and allow it to be emitted."""
        record.request_id = request_id_context.get()
        return True


def configure_logging(settings: Settings) -> None:
    """Configure application and Uvicorn loggers with a shared format."""
    level = logging.getLevelName(settings.log_level)
    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
    request_filter = RequestIdFilter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(request_filter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(level)
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured through the centralized logging setup."""
    return logging.getLogger(name)
