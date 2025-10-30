import httpx
import pytest

from app.config.settings import reload_settings, settings
from app.http import ApigeeTokenProvider, openai_client as openai_module
from app.http.openai_client import OpenAICompatibleClient


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [
        "APIGEE_TOKEN_URL",
        "APIGEE_CLIENT_ID",
        "APIGEE_CLIENT_SECRET",
        "APIGEE_AUDIENCE",
        "OPENAI_BASE_URL",
        "HTTP_RETRY_MAX_ATTEMPTS",
        "HTTP_MAX_RETRIES",
        "HTTP_TIMEOUT_SECONDS",
        "HTTP_RETRY_BACKOFF_MS",
        "HTTP_RETRY_BASE_DELAY",
    ]:
        monkeypatch.delenv(key, raising=False)
    reload_settings()
    yield
    reload_settings()


class _MockGatewayTransport(httpx.MockTransport):
    def __init__(self, sequence: list[int | str] | None = None) -> None:
        self.sequence = sequence or [200]
        self.calls: list[httpx.Request] = []
        self.token_calls = 0

        token_url = settings.apigee_token_url
        openai_base = settings.openai_base_url

        def handler(request: httpx.Request) -> httpx.Response:
            if token_url and request.url == httpx.URL(token_url):
                self.token_calls += 1
                return httpx.Response(
                    200,
                    json={"access_token": "abc123", "expires_in": 300},
                )
            if openai_base and str(request.url).startswith(openai_base):
                index = min(len(self.calls), len(self.sequence) - 1)
                action = self.sequence[index]
                self.calls.append(request)
                if isinstance(action, int):
                    if action != 200:
                        return httpx.Response(action, json={"error": "retry"})
                    return httpx.Response(200, json={"ok": True})
                if action == "stream":
                    body = (
                        b"event: response.created\n"
                        b"data: {\"status\":\"in_progress\"}\n\n"
                        b"event: response.completed\n"
                        b"data: {\"output_text\":\"hi\",\"usage\":{\"output_tokens\":1}}\n\n"
                    )
                    return httpx.Response(
                        200,
                        headers={"Content-Type": "text/event-stream"},
                        content=body,
                    )
                if action == "timeout":
                    raise httpx.ReadTimeout("timeout", request=request)
                if action == "transport":
                    raise httpx.TransportError("transport failure")
            return httpx.Response(404)

        super().__init__(handler)


@pytest.mark.anyio
async def test_post_responses_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    reload_settings()

    transport = _MockGatewayTransport()
    client = OpenAICompatibleClient(transport=transport)
    try:
        response = await client.post_responses(
            {"model": "test", "input": []},
            tenant_id="tenant",
            correlation_id="corr",
            request_id="req",
            telemetry="basic",
        )
    finally:
        await client.aclose()

    assert response["ok"] is True
    assert transport.token_calls == 1
    request = transport.calls[0]
    assert request.headers["Authorization"] == "Bearer abc123"
    assert request.headers["X-Correlation-Id"] == "corr"
    assert request.headers["X-Request-Id"] == "req"
    assert request.headers["X-Tenant-Id"] == "tenant"
    assert request.headers["X-Telemetry"] == "basic"


@pytest.mark.anyio
async def test_retries_on_transient_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    monkeypatch.setenv("HTTP_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("HTTP_MAX_RETRIES", "3")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("HTTP_RETRY_BACKOFF_MS", "10")
    monkeypatch.setenv("HTTP_RETRY_BASE_DELAY", "0.01")
    reload_settings()

    transport = _MockGatewayTransport(sequence=[429, 502, 200])
    client = OpenAICompatibleClient(transport=transport)
    try:
        result = await client.post_responses(
            {"model": "test", "input": []},
            tenant_id="tenant",
            correlation_id="corr",
            request_id="req",
        )
    finally:
        await client.aclose()

    assert result["ok"] is True
    assert len(transport.calls) == 3


@pytest.mark.anyio
async def test_missing_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    reload_settings()

    transport = _MockGatewayTransport()
    client = OpenAICompatibleClient(transport=transport)
    try:
        with pytest.raises(RuntimeError):
            await client.post_responses({"model": "x"}, tenant_id=None)
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_request_uses_global_client_and_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    monkeypatch.setenv("APIGEE_EXTRA_HEADERS_JSON", '{"X-Static":"value"}')
    reload_settings()

    transport = _MockGatewayTransport()
    original_make = openai_module._make_async_client

    def patched_make(transport_override: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
        return original_make(transport=transport_override or transport)

    monkeypatch.setattr(openai_module, "_make_async_client", patched_make)
    monkeypatch.setattr(openai_module, "_client", None)

    token_provider = ApigeeTokenProvider(transport=transport)
    client = OpenAICompatibleClient(token_provider=token_provider)
    try:
        response = await client.request(
            "POST",
            "/v1/responses",
            json_body={"model": "test", "input": []},
            tenant_id="tenant",
            correlation_id="corr",
            request_id="req",
            extra_headers={"X-Client-Id": "override", "X-New": "value"},
        )
    finally:
        await client.aclose()
        global_client = openai_module._client
        if global_client is not None:
            await global_client.aclose()
            openai_module._client = None

    assert response.status_code == 200
    last_request = transport.calls[-1]
    assert last_request.headers["X-Static"] == "value"
    assert last_request.headers["X-New"] == "value"
    assert last_request.headers["X-Client-Id"] == "override"
    assert last_request.headers["X-Correlation-Id"] == "corr"


@pytest.mark.anyio
async def test_non_retryable_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    reload_settings()

    transport = _MockGatewayTransport(sequence=[400])
    client = OpenAICompatibleClient(transport=transport)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.post_responses(
                {"model": "test", "input": []},
                tenant_id="tenant",
                correlation_id="corr",
                request_id="req",
            )
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_transport_error_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    monkeypatch.setenv("HTTP_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("HTTP_MAX_RETRIES", "3")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("HTTP_RETRY_BACKOFF_MS", "10")
    monkeypatch.setenv("HTTP_RETRY_BASE_DELAY", "0.0")
    reload_settings()

    transport = _MockGatewayTransport(sequence=["timeout", 200])
    client = OpenAICompatibleClient(transport=transport)
    try:
        result = await client.post_responses(
            {"model": "test", "input": []},
            tenant_id="tenant",
            correlation_id="corr",
            request_id="req",
        )
    finally:
        await client.aclose()

    assert result["ok"] is True
    assert len(transport.calls) == 2


@pytest.mark.anyio
async def test_transport_error_exhausts_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    monkeypatch.setenv("HTTP_RETRY_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("HTTP_MAX_RETRIES", "1")
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("HTTP_RETRY_BACKOFF_MS", "0")
    monkeypatch.setenv("HTTP_RETRY_BASE_DELAY", "0.0")
    reload_settings()

    transport = _MockGatewayTransport(sequence=["transport"])
    client = OpenAICompatibleClient(transport=transport)
    try:
        with pytest.raises(httpx.TransportError):
            await client.post_responses(
                {"model": "test", "input": []},
                tenant_id="tenant",
                correlation_id="corr",
                request_id="req",
            )
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_post_responses_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIGEE_AUDIENCE", "aud")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw/openai")
    reload_settings()

    transport = _MockGatewayTransport(sequence=["stream"])
    client = OpenAICompatibleClient(transport=transport)
    lines = []
    try:
        async for line in client.post_responses_stream(
            {"model": "test", "input": []},
            tenant_id="tenant",
            correlation_id="corr",
            request_id="req",
        ):
            lines.append(line)
    finally:
        await client.aclose()

    assert any("response.created" in entry for entry in lines)
    assert any("response.completed" in entry for entry in lines)
