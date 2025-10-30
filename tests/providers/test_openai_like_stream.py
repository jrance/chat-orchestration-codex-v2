from typing import AsyncIterator

import pytest

from app.providers.openai_like import responses
from app.providers.openai_like.sse import parse_sse


class DummyClient:
    def __init__(self, events: list[str]):
        self._events = events
        self.calls = []

    async def post_responses(self, body, **kwargs):
        self.calls.append(("sync", body, kwargs))
        return {"output_text": "final", "usage": {"output_tokens": 1}}

    async def post_responses_stream(self, body, **kwargs) -> AsyncIterator[str]:
        self.calls.append(("stream", body, kwargs))
        for line in self._events:
            yield line


@pytest.mark.anyio
async def test_create_response_uses_headers():
    client = DummyClient([])
    body = {"model": "test", "input": []}
    context_headers = {
        "X-Tenant-Id": "tenant",
        "X-Correlation-Id": "corr",
        "X-Request-Id": "req",
        "X-Telemetry": "basic",
        "X-Custom": "value",
    }

    result = await responses.create_response(body, context_headers=context_headers, client=client)

    assert result["output_text"] == "final"
    call = client.calls[0]
    assert call[0] == "sync"
    assert call[2]["tenant_id"] == "tenant"
    assert call[2]["extra_headers"]["X-Custom"] == "value"


@pytest.mark.anyio
async def test_stream_response_parses_events():
    events = [
        "event: response.created",
        "data: {\"status\":\"in_progress\"}",
        "",
        "event: response.completed",
        "data: {\"output_text\":\"done\"}",
        "",
    ]
    client = DummyClient(events)
    body = {"model": "test", "input": []}

    collected = []
    async for evt in responses.stream_response(body, client=client):
        collected.append(evt)

    assert collected[0]["type"] == "response.created"
    assert collected[-1]["output_text"] == "done"


@pytest.mark.anyio
async def test_parse_sse_handles_partial_json():
    async def _lines():
        yield "event: response.created"
        yield "data: {\"status\":\"ok\"}"
        yield ""
        yield "event: response.output_text.delta"
        yield "data: not-json"
        yield ""

    result = []
    async for message in parse_sse(_lines()):
        result.append(message)

    assert result[0]["type"] == "response.created"
    assert result[1]["data"] == "not-json"
