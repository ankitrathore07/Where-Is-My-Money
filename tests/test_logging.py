import json
import logging

from app.core.logging import RedactedJsonFormatter, configure_logging, redact_message


def test_json_formatter_emits_only_safe_structured_fields() -> None:
    formatter = RedactedJsonFormatter()
    record = logging.LogRecord(
        name="where_is_my_money.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="import.completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.workspace_id = 42
    record.row_count = 7
    record.email = "private@example.com"

    payload = json.loads(formatter.format(record))

    assert payload["event"] == "import.completed"
    assert payload["request_id"] == "req-123"
    assert payload["workspace_id"] == 42
    assert payload["row_count"] == 7
    assert "email" not in payload


def test_free_form_log_message_redacts_credentials_and_identity() -> None:
    message = (
        "authorization=Bearer-secret cookie=session-value "
        "email=person@example.com Bearer abc.def.ghi"
    )

    redacted = redact_message(message)

    assert "Bearer-secret" not in redacted
    assert "session-value" not in redacted
    assert "person@example.com" not in redacted
    assert "abc.def.ghi" not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_logging_disables_raw_uvicorn_access_lines() -> None:
    logger_names = ("where_is_my_money", "uvicorn", "uvicorn.error", "uvicorn.access")
    original = {
        name: (
            logging.getLogger(name).handlers.copy(),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            logging.getLogger(name).disabled,
        )
        for name in logger_names
    }
    try:
        configure_logging()

        access_logger = logging.getLogger("uvicorn.access")
        assert access_logger.disabled is True
        assert access_logger.handlers == []
        assert logging.getLogger("where_is_my_money").handlers
    finally:
        for name, (handlers, level, propagate, disabled) in original.items():
            restored = logging.getLogger(name)
            restored.handlers = handlers
            restored.setLevel(level)
            restored.propagate = propagate
            restored.disabled = disabled
