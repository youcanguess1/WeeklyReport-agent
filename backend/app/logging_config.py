from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any


SYSTEM_LOGGER_NAME = "app"
_HANDLER_MARKER = "_weekly_report_system_log_handler"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
            "component": getattr(record, "component", None),
            "task_id": getattr(record, "task_id", None),
            "trace_id": getattr(record, "trace_id", None),
            "exception_type": None,
            "stack": None,
        }
        if record.exc_info:
            exc_type = record.exc_info[0]
            payload["exception_type"] = exc_type.__name__ if exc_type else None
            payload["stack"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_system_logging(settings: Any) -> logging.Logger:
    logger = logging.getLogger(SYSTEM_LOGGER_NAME)
    _remove_existing_system_handlers(logger)

    if not getattr(settings, "enabled", True):
        logger.propagate = True
        return logger

    log_path = getattr(settings, "path")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=int(getattr(settings, "max_bytes", 10_485_760)),
            backupCount=int(getattr(settings, "backup_count", 5)),
            encoding="utf-8",
        )
    except OSError:
        # A read-only mounted data directory must not prevent the API from
        # starting; retain the same JSON formatter on stderr instead.
        handler = logging.StreamHandler()
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(JsonLineFormatter())

    level_name = str(getattr(settings, "level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    handler.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _remove_existing_system_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()
