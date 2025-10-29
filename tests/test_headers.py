import pytest

from app.config.settings import reload_settings
from app.http.headers import (
    H_CLIENT_ID,
    H_CORRELATION_ID,
    H_REQUEST_ID,
    H_TELEMETRY,
    H_TENANT_ID,
    H_TIMESTAMP,
    build_default_headers,
)


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

    headers = build_default_headers("tenant-x", "corr-y", "req-z", telemetry="verbose")

    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    assert headers[H_TENANT_ID] == "tenant-x"
    assert headers[H_CORRELATION_ID] == "corr-y"
    assert headers[H_REQUEST_ID] == "req-z"
    assert headers[H_CLIENT_ID] == "client-123"
    assert headers[H_TELEMETRY] == "verbose"
    assert headers["X-Feature"] == "enabled"
    assert H_TIMESTAMP in headers


def test_extra_headers_do_not_override_explicit_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "APIGEE_EXTRA_HEADERS_JSON",
        '{"X-Telemetry":"basic","X-Correlation-Id":"from-config"}',
    )
    reload_settings()

    headers = build_default_headers("tenant-x", "corr-y", "req-z", telemetry="verbose")

    assert headers[H_CORRELATION_ID] == "corr-y"
    assert headers[H_TELEMETRY] == "verbose"
