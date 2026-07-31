"""Structured JSON logging configuration.

Uses only the standard library (no extra dependency) to satisfy the
constitution's "no dependencies without approval" restriction while still
producing structured logs as mandated by the constitution's observability
standard.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        reserved = logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in reserved and key not in payload
        }
        if extras:
            payload["extra"] = extras

        return json.dumps(payload, default=str)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure the root logger to emit structured JSON to stdout."""
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JSONFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())
