from __future__ import annotations

import json
import logging
import sys

from app.logging_config import JsonLineFormatter, configure_system_logging
from app.services.config_service import LoggingSettings


def test_json_line_formatter_outputs_base_fields() -> None:
    formatter = JsonLineFormatter()
    record = logging.LogRecord(
        name="app.services.demo",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="hello system log",
        args=(),
        exc_info=None,
    )
    record.event = "demo_event"
    record.component = "demo"
    record.task_id = "task-1"
    record.trace_id = "trace-1"

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.services.demo"
    assert payload["event"] == "demo_event"
    assert payload["message"] == "hello system log"
    assert payload["component"] == "demo"
    assert payload["task_id"] == "task-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["exception_type"] is None
    assert payload["stack"] is None


def test_json_line_formatter_outputs_exception_fields() -> None:
    formatter = JsonLineFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            name="app.services.demo",
            level=logging.ERROR,
            pathname=__file__,
            lineno=34,
            msg="failed",
            args=(),
            exc_info=None,
        )
        record.exc_info = sys.exc_info()

    payload = json.loads(formatter.format(record))

    assert payload["exception_type"] == "RuntimeError"
    assert "RuntimeError: boom" in payload["stack"]


def test_configure_system_logging_disabled_skips_file_handler(tmp_path) -> None:
    logger = configure_system_logging(
        LoggingSettings(enabled=False, path=tmp_path / "logs" / "disabled.jsonl")
    )

    assert logger.name == "app"
    assert not any(isinstance(handler.formatter, JsonLineFormatter) for handler in logger.handlers)
    assert not (tmp_path / "logs" / "disabled.jsonl").exists()


def test_configure_system_logging_uses_rotating_handler_settings(tmp_path) -> None:
    log_path = tmp_path / "logs" / "system.jsonl"
    logger = configure_system_logging(
        LoggingSettings(
            enabled=True,
            path=log_path,
            level="WARNING",
            max_bytes=2048,
            backup_count=3,
        )
    )

    handlers = [handler for handler in logger.handlers if isinstance(handler.formatter, JsonLineFormatter)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert getattr(handler, "maxBytes") == 2048
    assert getattr(handler, "backupCount") == 3
    assert handler.level == logging.WARNING
