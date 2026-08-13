"""Structured application logging with a deliberately small safe-field surface."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

SAFE_LOG_FIELDS = (
    "app_env",
    "duration_ms",
    "error_code",
    "method",
    "path",
    "request_id",
    "row_count",
    "state",
    "status_code",
    "user_id",
    "workspace_id",
)
SENSITIVE_VALUE = "[REDACTED]"
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie|csrf(?:_token)?|email|password|secret(?:_key)?|session|token)"
    r"\s*([=:])\s*([^\s&,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")


def redact_message(message: str) -> str:
    """Remove common credential and identity values from free-form messages."""
    redacted = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{SENSITIVE_VALUE}", message
    )
    return _BEARER_TOKEN.sub(f"Bearer {SENSITIVE_VALUE}", redacted)


class RedactedJsonFormatter(logging.Formatter):
    """Emit one JSON object per event and ignore unapproved record attributes."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact_message(record.getMessage()),
        }
        for field in SAFE_LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and record.exc_info[0]:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging() -> None:
    """Configure application/server JSON logs and replace unsafe access logs."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactedJsonFormatter())
    for logger_name in ("where_is_my_money", "uvicorn", "uvicorn.error"):
        configured_logger = logging.getLogger(logger_name)
        configured_logger.handlers.clear()
        configured_logger.addHandler(handler)
        configured_logger.setLevel(logging.INFO)
        configured_logger.propagate = False
        configured_logger.disabled = False

    # The application emits a safer route-template request event with a request ID.
    # Uvicorn's raw request line can include OAuth query credentials.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True


logger = logging.getLogger("where_is_my_money")
