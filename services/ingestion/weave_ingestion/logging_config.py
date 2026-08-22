"""Structured JSON logging for Cloud Run.

Without this the app's own INFO records are dropped (the root logger defaults
to WARNING) and every observability assertion in the build plan silently fails.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class CloudLoggingFormatter(logging.Formatter):
    """Emit Cloud Logging's expected JSON shape, keeping `extra` fields intact."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _STANDARD}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
