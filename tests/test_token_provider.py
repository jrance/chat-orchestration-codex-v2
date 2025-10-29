import pytest
import httpx

from app.config.settings import reload_settings
from app.http.token_provider import ApigeeTokenProvider


class _MockTokenTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            if "client_id" not in request.content.decode():
                return httpx.Response(400, json={"error": "bad"})
            return httpx.Response(200, json={"access_token": "abc123", "expires_in": 120})

        super().__init__(handler)


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [
        "APIGEE_TOKEN_URL",
        "APIGEE_CLIENT_ID",
        "APIGEE_CLIENT_SECRET",
        "APIGEE_SCOPES",
    ]:
        monkeypatch.delenv(key, raising=False)
    reload_settings()
    yield
    reload_settings()


@pytest.mark.anyio
async def test_token_caches_and_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    reload_settings()

    transport = _MockTokenTransport()
    provider = ApigeeTokenProvider(transport=transport)

    token_one = await provider.get_token()
    token_two = await provider.get_token()

    assert token_one == "abc123"
    assert token_two == "abc123"
    assert transport.calls == 1  # cached token reused


@pytest.mark.anyio
async def test_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIGEE_TOKEN_URL", "https://gw/token")
    monkeypatch.setenv("APIGEE_CLIENT_ID", "client")
    monkeypatch.setenv("APIGEE_CLIENT_SECRET", "secret")
    reload_settings()

    transport = _MockTokenTransport()
    provider = ApigeeTokenProvider(transport=transport)

    await provider.get_token()
    await provider.get_token(force_refresh=True)

    assert transport.calls == 2


@pytest.mark.anyio
async def test_missing_configuration_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIGEE_TOKEN_URL", raising=False)
    monkeypatch.delenv("APIGEE_CLIENT_ID", raising=False)
    monkeypatch.delenv("APIGEE_CLIENT_SECRET", raising=False)
    reload_settings()

    provider = ApigeeTokenProvider()
    with pytest.raises(RuntimeError):
        await provider.get_token()
