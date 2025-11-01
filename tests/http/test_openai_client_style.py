import json
from typing import Any

import httpx
import pytest

from app.config.settings import settings
from app.http.openai_client import OpenAICompatibleClient


class _StaticTokenProvider:
    async def get_token(self) -> str:
        return "static-token"


class _FallbackTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[str, httpx.Request]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/v1/responses"):
                if not self.calls:
                    self.calls.append(("responses", request))
                    return httpx.Response(
                        400,
                        json={"error": {"message": "Missing required parameter: 'messages'"}},
                    )
            if request.url.path.endswith("/v1/chat/completions"):
                self.calls.append(("chat", request))
                return httpx.Response(
                    200,
                    json={
                        "id": "chat-1",
                        "choices": [
                            {"message": {"role": "assistant", "content": "fallback"}}
                        ],
                        "usage": {"output_tokens": 2},
                    },
                )
            return httpx.Response(404)

        super().__init__(handler)


class _ChatTransport(httpx.MockTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            if request.url.path.endswith("/v1/chat/completions"):
                return httpx.Response(
                    200,
                    json={
                        "id": "chat-2",
                        "choices": [
                            {"message": {"role": "assistant", "content": "direct"}}
                        ],
                        "usage": {"output_tokens": 1},
                    },
                )
            return httpx.Response(404)

        super().__init__(handler)


@pytest.mark.anyio
async def test_responses_style_falls_back_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://gw/openai")
    monkeypatch.setattr(settings, "openai_api_style", "responses")
    monkeypatch.setattr(settings, "openai_responses_fallback_to_chat", True)

    transport = _FallbackTransport()
    client = OpenAICompatibleClient(token_provider=_StaticTokenProvider(), transport=transport)
    try:
        result = await client.post_responses(
            {
                "model": "gpt-4o",
                "input": [{"role": "user", "content": "hello"}],
            }
        )
    finally:
        await client.aclose()

    assert result["output_text"] == "fallback"
    assert [call[0] for call in transport.calls] == ["responses", "chat"]

    fallback_request = transport.calls[1][1]
    body_json = json.loads(fallback_request.content.decode("utf-8"))
    assert fallback_request.url.path.endswith("/v1/chat/completions")
    assert "messages" in body_json and body_json["messages"][0]["role"] == "user"


@pytest.mark.anyio
async def test_chat_style_uses_chat_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://gw/openai")
    monkeypatch.setattr(settings, "openai_api_style", "chat")
    monkeypatch.setattr(settings, "openai_responses_fallback_to_chat", True)

    transport = _ChatTransport()
    client = OpenAICompatibleClient(token_provider=_StaticTokenProvider(), transport=transport)
    try:
        result = await client.post_responses(
            {
                "model": "gpt-4o",
                "input": [{"role": "user", "content": "hello"}],
            }
        )
    finally:
        await client.aclose()

    assert transport.calls, "expected chat endpoint to be invoked"
    request = transport.calls[0]
    assert request.url.path.endswith("/v1/chat/completions")
    body_json: dict[str, Any] = json.loads(request.content.decode("utf-8"))
    assert "messages" in body_json
    assert body_json["messages"][0]["role"] == "user"
    assert result["output_text"] == "direct"
