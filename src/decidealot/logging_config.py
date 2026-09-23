"""JSON logging with request scope propagation and secret redaction."""

import contextvars
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from re import IGNORECASE
from re import compile as compile_pattern
from typing import Any, cast

from pythonjsonlogger.json import JsonFormatter

_scope: contextvars.ContextVar[dict[str, object] | None] = contextvars.ContextVar(
    "log_scope", default=None
)
_secret_key_pattern = compile_pattern(
    r"password|token|secret|api[_-]?key|authorization|cookie", IGNORECASE
)
_log_file_max_bytes = 50_000_000
_log_file_backups = 5
_timestamp_precision = "milliseconds"
_utc_offset_suffix = "+00:00"
_utc_timestamp_suffix = "Z"


def set_scope(**attributes: object) -> contextvars.Token[dict[str, object] | None]:
    """Layer attributes onto the request-scoped structured log context."""

    return _scope.set({**(_scope.get() or {}), **attributes})


def reset_scope(token: contextvars.Token[dict[str, object] | None]) -> None:
    """Restore the structured log scope after a request ends."""

    _scope.reset(token)


class ScopeFilter(logging.Filter):
    """Attach current ContextVar attributes to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_scope.get() or {}).items():
            setattr(record, key, value)
        return True


class RedactingFormatter(JsonFormatter):
    """Prevent accidentally attached secret values from reaching logs."""

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_data, record, message_dict)
        self._redact(log_data)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Render every JSON log timestamp as UTC with millisecond precision."""

        del datefmt
        return (
            datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec=_timestamp_precision)
            .replace(_utc_offset_suffix, _utc_timestamp_suffix)
        )

    def _redact(self, value: object, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            dictionary = cast(dict[object, object], value)
            for key, nested_value in dictionary.items():
                if _secret_key_pattern.search(str(key)):
                    dictionary[key] = "[REDACTED]"
                else:
                    self._redact(nested_value, depth + 1)
        elif isinstance(value, list):
            for nested_value in cast(list[object], value):
                self._redact(nested_value, depth + 1)


def configure_logging(level: str, log_file: Path) -> None:
    """Configure stderr and rotating-file JSON handlers once at process entry."""

    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(filename)s %(lineno)d %(funcName)s %(message)s",
        rename_fields={
            "asctime": "time",
            "levelname": "level",
            "filename": "file",
            "lineno": "line",
            "funcName": "func",
            "message": "msg",
        },
        datefmt="%Y-%m-%dT%H:%M:%S.%fZ",
    )
    scope_filter = ScopeFilter()
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(
            log_file,
            maxBytes=_log_file_max_bytes,
            backupCount=_log_file_backups,
            encoding="utf-8",
        ),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(scope_filter)
    logging.basicConfig(level=level, handlers=handlers, force=True)
