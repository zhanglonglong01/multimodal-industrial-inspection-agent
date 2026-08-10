"""Small JSON logging setup with no external logging dependency."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format one log record as a compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        context = getattr(record, "context", None)
        if event:
            payload["event"] = event
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once for CLI and service entry points."""

    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **context: Any,
) -> None:
    logger.log(level, event, extra={"event": event, "context": context})

