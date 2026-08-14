import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app, create_app


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio for the asynchronous FastAPI tests."""
    return "asyncio"


def test_ai_graph_is_absent_when_feature_is_disabled() -> None:
    application = create_app(Settings(_env_file=None, app_env="test", secret_key="test-secret"))

    assert application.state.categorization_graph is None


def test_ai_graph_is_built_only_when_enabled_with_a_key() -> None:
    application = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            secret_key="test-secret",
            openai_api_key="synthetic-key",
            openai_categorization_enabled=True,
        )
    )

    assert application.state.categorization_graph is not None


@pytest.mark.anyio
async def test_health_check_returns_ok() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_home_page_renders() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "Understand where your money goes." in response.text


@pytest.mark.anyio
async def test_response_includes_request_reference_and_security_headers() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health", headers={"X-Request-ID": "test-request-123"})

    assert response.headers["x-request-id"] == "test-request-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_invalid_request_reference_is_replaced() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health", headers={"X-Request-ID": "contains spaces"})

    request_id = response.headers["x-request-id"]
    assert request_id != "contains spaces"
    assert len(request_id) == 32


@pytest.mark.anyio
async def test_request_log_uses_route_template_instead_of_bearer_token() -> None:
    application = create_app(Settings(_env_file=None, app_env="test", secret_key="test-secret"))
    records: list[logging.LogRecord] = []

    class RecordingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    @application.get("/invitations/{raw_token}/synthetic")
    async def invitation_route(raw_token: str) -> dict[str, bool]:
        return {"ok": bool(raw_token)}

    request_logger = logging.getLogger("where_is_my_money.request")
    handler = RecordingHandler()
    request_logger.addHandler(handler)
    request_logger.setLevel(logging.INFO)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/invitations/private-bearer-value/synthetic")
    finally:
        request_logger.removeHandler(handler)

    assert response.status_code == 200
    request_record = next(record for record in records if record.msg == "request.completed")
    assert request_record.path == "/invitations/{raw_token}/synthetic"
    assert all("private-bearer-value" not in record.getMessage() for record in records)


@pytest.mark.anyio
async def test_unknown_invitation_path_is_redacted_in_request_log() -> None:
    application = create_app(Settings(_env_file=None, app_env="test", secret_key="test-secret"))
    records: list[logging.LogRecord] = []

    class RecordingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    request_logger = logging.getLogger("where_is_my_money.request")
    handler = RecordingHandler()
    request_logger.addHandler(handler)
    request_logger.setLevel(logging.INFO)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/invitations/private-bearer-value/not-a-route")
    finally:
        request_logger.removeHandler(handler)

    assert response.status_code == 404
    request_record = next(record for record in records if record.msg == "request.completed")
    assert request_record.path == "[unmatched]"


@pytest.mark.anyio
async def test_untrusted_host_is_rejected() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://attacker.example",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 400
    assert response.text == "Invalid host header"


@pytest.mark.anyio
async def test_not_found_uses_safe_html_error_page() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/does-not-exist", headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert "That page could not be found." in response.text
    assert response.headers["x-request-id"] in response.text


@pytest.mark.anyio
async def test_unhandled_exception_uses_safe_html_error_page() -> None:
    application = create_app(Settings(_env_file=None, app_env="test", secret_key="test-secret"))

    @application.get("/synthetic-failure")
    async def synthetic_failure() -> None:
        raise RuntimeError("private financial detail")

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/synthetic-failure",
            headers={"X-Request-ID": "failed-request-123", "Accept": "text/html"},
        )

    assert response.status_code == 500
    assert "Something went wrong." in response.text
    assert "private financial detail" not in response.text
    assert "failed-request-123" in response.text
    assert response.headers["x-request-id"] == "failed-request-123"
