import pytest

from app.api.deps import ExecutionContext
from app.api.models import ExecutionHeaders
from app.config.settings import reload_settings
from app.http.headers import H_CLIENT_ID, H_CORRELATION_ID, H_REQUEST_ID, H_TENANT_ID, H_TIMESTAMP, build_default_headers


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [
        "APIGEE_CLIENT_ID",
        "APIGEE_EXTRA_HEADERS_JSON",
    ]:
        monkeypatch.delenv(key, raising=False)
    reload_settings()
    yield
    reload_settings()


def test_build_default_headers_sets_core_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client-123")
    monkeypatch.setenv("APIGEE_EXTRA_HEADERS_JSON", '{"X-Feature":"enabled"}')
    reload_settings()

    headers = build_default_headers("tenant-x", "corr-y", "req-z")

    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    assert headers[H_TENANT_ID] == "tenant-x"
    assert headers[H_CORRELATION_ID] == "corr-y"
    assert headers[H_REQUEST_ID] == "req-z"
    assert headers[H_CLIENT_ID] == "client-123"
    assert headers["X-Feature"] == "enabled"
    assert H_TIMESTAMP in headers


def test_extra_headers_do_not_override_explicit_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "APIGEE_EXTRA_HEADERS_JSON",
        '{"X-Telemetry":"basic","X-Correlation-Id":"from-config"}',
    )
    reload_settings()

    headers = build_default_headers("tenant-x", "corr-y", "req-z")

    assert headers[H_CORRELATION_ID] == "corr-y"
    assert "X-Telemetry" not in headers


def test_execution_context_to_http_headers_includes_optional_fields() -> None:
    headers_model = ExecutionHeaders(
        tenant_id="tenant-1",
        client_id="client-123",
        telemetry="basic",
        extra_headers={"X-Extra-Feature": "enabled"},
    )
    context = ExecutionContext(headers=headers_model)
    http_headers = context.to_http_headers()

    assert http_headers["X-Tenant-Id"] == "tenant-1"
    assert http_headers["X-Client-Id"] == "client-123"
    assert "X-Telemetry" not in http_headers
    assert http_headers["X-Extra-Feature"] == "enabled"
