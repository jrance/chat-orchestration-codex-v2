import importlib
import ssl

import httpx
import pytest

from app.config.settings import reload_settings

CERT_BASE64 = (
    "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tDQpNSUlEc2pDQ0FwcWdBd0lCQWdJUUZSa2t6amh3TmJs"
    "TUxTRVZiTG4xMHpBTkJna3Foa2lHOXcwQkFRc0ZBREJuDQpNU3N3S1FZRFZRUUxEQ0pEY21WaGRHVmtJ"
    "R0o1SUdoMGRIQTZMeTkzZDNjdVptbGtaR3hsY2pJdVkyOXRNUlV3DQpFd1lEVlFRS0RBeEVUMTlPVDFS"
    "ZlZGSlZVMVF4SVRBZkJnTlZCQU1NR0VSUFgwNVBWRjlVVWxWVFZGOUdhV1JrDQpiR1Z5VW05dmREQWVG"
    "dzB5TlRBeU1ERXhNRFExTkRsYUZ3MHlOekExTURJeE1EUTFORGxhTUdjeEt6QXBCZ05WDQpCQXNNSWtO"
    "eVpXRjBaV1FnWW5rZ2FIUjBjRG92TDNkM2R5NW1hV1JrYkdWeU1pNWpiMjB4RlRBVEJnTlZCQW9NDQpE"
    "RVJQWDA1UFZGOVVVbFZUVkRFaE1COEdBMVVFQXd3WVJFOWZUazlVWDFSU1ZWTlVYMFpwWkdSc1pYSlNi"
    "MjkwDQpNSUlCSWpBTkJna3Foa2lHOXcwQkFRRUZBQU9DQVE4QU1JSUJDZ0tDQVFFQXFvbWhDQTA2Zml0"
    "eVNURU8wU1UrDQpReXYyMmdNMXJ1V01UbXZEYmJPTlNPOVpsZHJVZ0hCRG41dmcvVzEvdzFIcmZPOEJs"
    "eFlueGgxU3hCN3djc0NiDQpQNzJZdWpUM0NaV25haHlEVE9iSTFrRFNkckFFZlRSREdiTW5jbTV0R3JT"
    "MGgwSkJYREwrK2NVdHRtNzRwQlNhDQpXYURUcXZFZThPY3ZUWXo1S1NQb0NxQWtGNjFGdG9iUHVEUm8w"
    "RVdOL0k3UFFTQ01zM1F4V085VGdyc0R0R1Z0DQozVjEraTJPYzNRWEIwbWl3MUk2Sy9DaTR3Y3R2aDl1"
    "dTJGcHZVMWp4SHNkZTFnaUNrTW9iQ1lQOFR4TWFQcWthDQppVkhRLy9jcDdEcXpUTTZHTTFxSEVQaW0y"
    "cDNCQUlFZTRMaWhlZEUyWVlWanJhTG1PMWdlTXQrOEJMTnRTV2R1DQpnUUlEQVFBQm8xb3dXREFUQmdO"
    "VkhTVUVEREFLQmdnckJnRUZCUWNEQVRBU0JnTlZIUk1CQWY4RUNEQUdBUUgvDQpBZ0VBTUIwR0ExVWRE"
    "Z1FXQkJTVlFodWQ1RlFKQW5TSDdveGV1UHZhZUFzRXJUQU9CZ05WSFE4QkFmOEVCQU1DDQpBUVl3RFFZ"
    "SktvWklodmNOQVFFTEJRQURnZ0VCQURMQVNqTysycDQ0TlpRNUZwWThOeElmWWdvbEp2T2tlRGxkDQo5"
    "dmFNNDY1d1dPQWxwUm5sTkw5M3JEZlJqRTEvY2RHQldnNXIvZnUyeXVDZzJ1UXN5aFJ1OEZzNFhhSVVv"
    "WC9NDQpDMGN1V3k4SWRjc01oT2JZRHpWb0hrWi90TUtscDNjd1krU2oyaEVsZHg5NEpjVlVOTTQ5UWNT"
    "bUZIcXR0cmhUDQpLMm4rMTRaUE8rQXRkbGVON1Z5SDRPdG9mMzgvelZSbWVEVkorM2dFcE1jOTdEajVK"
    "TUNVZGhEbUQ5bWtmYXJiDQpWTmtSWHB1aFJ0Z2RLeWpWc1ExNnVqN2t1N3l3UXhLb0o2cHNmOVpZUlly"
    "UjIzaE5uc3dkMnFBaFBkeTQ0bUVLDQplZ0lGNVpWUTdjajlDd05rWmdwcTVBd012c1JpdVVzN2lkVHMw"
    "Z1VNTHVYd1VBVTlVUjA9DQotLS0tLUVORCBDRVJUSUZJQ0FURS0tLS0tDQ=="
)


class CaptureClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    async def aclose(self) -> None:  # pragma: no cover - not used directly
        self.closed = True

    def close(self) -> None:
        self.closed = True


def _set_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_URL", "http://127.0.0.1:8888")
    monkeypatch.setenv("PROXY_CA_BUNDLE", CERT_BASE64)
    reload_settings()


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROXY_ENABLED", raising=False)
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.delenv("PROXY_CA_BUNDLE", raising=False)
    reload_settings()


def _reload_factory():
    import app.http.client_factory as cf

    importlib.reload(cf)
    return cf


@pytest.mark.parametrize(
    "preferred, expected_key",
    [
        ("proxies", "proxies"),
        ("proxy", "proxy"),
    ],
)
def test_proxy_enabled_signature_branches(monkeypatch: pytest.MonkeyPatch, preferred: str, expected_key: str) -> None:
    _set_proxy_env(monkeypatch)
    cf = _reload_factory()

    monkeypatch.setattr(httpx, "AsyncClient", CaptureClient)
    monkeypatch.setattr(cf, "_preferred_proxy_argument", lambda: preferred)

    try:
        client = cf.create_gateway_client()
        assert isinstance(client, CaptureClient)

        kwargs = client.kwargs  # type: ignore[attr-defined]
        assert kwargs[expected_key] == (
            "http://127.0.0.1:8888" if expected_key == "proxy" else {"all": "http://127.0.0.1:8888"}
        )
        assert expected_key in kwargs
        if expected_key == "proxy":
            assert kwargs["proxy"] == "http://127.0.0.1:8888"
            assert "proxies" not in kwargs
        else:
            assert kwargs["proxies"] == {"all": "http://127.0.0.1:8888"}
            assert "proxy" not in kwargs
        assert "transport" not in kwargs
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["trust_env"] is False
    finally:
        cf.reset_clients()
        _clear_proxy_env(monkeypatch)


def test_proxy_enabled_transport_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_proxy_env(monkeypatch)
    cf = _reload_factory()

    transport_record: dict[str, object] = {}

    class DummyTransport:
        def __init__(self, *args, **kwargs):
            transport_record["args"] = args
            transport_record["kwargs"] = kwargs
            transport_record["instance"] = self

    monkeypatch.setattr(httpx, "AsyncClient", CaptureClient)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", DummyTransport)
    monkeypatch.setattr(cf, "_preferred_proxy_argument", lambda: "transport")

    try:
        client = cf.create_gateway_client()
        assert isinstance(client, CaptureClient)

        kwargs = client.kwargs  # type: ignore[attr-defined]
        assert "transport" in kwargs
        assert kwargs["transport"] is transport_record["instance"]
        assert transport_record["kwargs"] == {"proxy": "http://127.0.0.1:8888"}
        assert "proxies" not in kwargs
        assert "proxy" not in kwargs
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["trust_env"] is False
    finally:
        cf.reset_clients()
        _clear_proxy_env(monkeypatch)


def test_proxy_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_ENABLED", "false")
    monkeypatch.setenv("PROXY_URL", "")
    monkeypatch.setenv("PROXY_CA_BUNDLE", "")
    reload_settings()

    cf = _reload_factory()
    monkeypatch.setattr(httpx, "AsyncClient", CaptureClient)

    try:
        client = cf.create_gateway_client()
        assert isinstance(client, CaptureClient)

        kwargs = client.kwargs  # type: ignore[attr-defined]
        assert kwargs.get("proxies") in (None, {})
        assert kwargs.get("proxy") in (None, {})
        assert "transport" not in kwargs
        assert kwargs["verify"] is True
        assert kwargs["trust_env"] is False
    finally:
        cf.reset_clients()
        _clear_proxy_env(monkeypatch)
