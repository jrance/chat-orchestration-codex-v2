import pytest
from pydantic import SecretStr

from app.config.settings import settings
from app.http.token_provider import ApigeeTokenProvider


@pytest.mark.anyio
async def test_token_provider_uses_api_key_when_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_api_key", "direct-key")
    provider = ApigeeTokenProvider()
    provider.clear_cache()

    token = await provider.get_token(force_refresh=True)
    assert token == "direct-key"


@pytest.mark.anyio
async def test_token_provider_fails_fast_without_oauth_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "apigee_token_url", None)
    monkeypatch.setattr(settings, "apigee_client_id", None)
    monkeypatch.setattr(settings, "apigee_client_secret", None)
    monkeypatch.setattr(settings, "apigee_scopes", "")
    monkeypatch.setattr(settings, "apigee_audience", None)

    provider = ApigeeTokenProvider()
    provider.clear_cache()

    with pytest.raises(RuntimeError):
        await provider.get_token(force_refresh=True)


@pytest.mark.anyio
async def test_token_provider_accepts_whitespace_configuration(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "apigee_token_url", " https://example.com/oauth ")
    monkeypatch.setattr(settings, "apigee_client_id", " client-id ")
    monkeypatch.setattr(settings, "apigee_client_secret", SecretStr(" client-secret "))
    monkeypatch.setattr(settings, "apigee_scopes", "scope1 scope2 ")
    monkeypatch.setattr(settings, "apigee_audience", " audience ")

    provider = ApigeeTokenProvider()

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "token", "expires_in": 120}

    class _FakeClient:
        async def post(self, url, data):
            assert url == "https://example.com/oauth"
            assert data["client_id"] == "client-id"
            assert data["client_secret"] == "client-secret"
            assert data["scope"] == "scope1 scope2"
            assert data["audience"] == "audience"
            return _FakeResponse()

    monkeypatch.setattr("app.http.token_provider.get_token_client", lambda: _FakeClient())
    token = await provider.get_token(force_refresh=True)
    assert token == "token"


@pytest.mark.anyio
async def test_token_provider_rejects_placeholder_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "apigee_token_url", "https://gateway.example.com/oauth2/token")
    monkeypatch.setattr(settings, "apigee_client_id", "replace-me")
    monkeypatch.setattr(settings, "apigee_client_secret", SecretStr("changeme"))
    monkeypatch.setattr(settings, "apigee_scopes", "")
    monkeypatch.setattr(settings, "apigee_audience", "")

    provider = ApigeeTokenProvider()

    with pytest.raises(RuntimeError):
        await provider.get_token(force_refresh=True)
