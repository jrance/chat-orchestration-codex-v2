import importlib
import ssl

import httpx

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


def test_proxy_enabled(monkeypatch):
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_URL", "http://127.0.0.1:8888")
    monkeypatch.setenv("PROXY_CA_BUNDLE", CERT_BASE64)
    reload_settings()

    import app.http.client_factory as cf

    importlib.reload(cf)
    real_client = httpx.AsyncClient
    try:
        httpx.AsyncClient = CaptureClient  # type: ignore[assignment]
        client = cf.get_gateway_client()
        assert isinstance(client, CaptureClient)
        kwargs = client.kwargs  # type: ignore[attr-defined]
        assert kwargs["proxies"] == {"all": "http://127.0.0.1:8888"}
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["trust_env"] is False
    finally:
        httpx.AsyncClient = real_client
        cf.reset_clients()
        reload_settings()


def test_proxy_disabled(monkeypatch):
    monkeypatch.setenv("PROXY_ENABLED", "false")
    monkeypatch.setenv("PROXY_URL", "")
    monkeypatch.setenv("PROXY_CA_BUNDLE", "")
    reload_settings()

    import app.http.client_factory as cf

    importlib.reload(cf)
    real_client = httpx.AsyncClient
    try:
        httpx.AsyncClient = CaptureClient  # type: ignore[assignment]
        client = cf.get_gateway_client()
        assert isinstance(client, CaptureClient)
        kwargs = client.kwargs  # type: ignore[attr-defined]
        assert kwargs.get("proxies") in (None, {})
        assert kwargs["verify"] is True
        assert kwargs["trust_env"] is False
    finally:
        httpx.AsyncClient = real_client
        cf.reset_clients()
        reload_settings()
