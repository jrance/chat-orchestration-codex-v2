import json

import pytest

from app.http.openai_client import _chat_stream_as_responses


class _FakeResponse:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.anyio
async def test_chat_stream_emits_indexed_tool_calls():
    chunk = {
        "id": "chatcmpl-001",
        "model": "gpt-4o",
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {
                                "name": "tool_ddgs_search",
                                "arguments": '{"query": "chips"}',
                            },
                        },
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "tool_news_search",
                                "arguments": '{"query": "apples"}',
                            },
                        },
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    lines = [f"data: {json.dumps(chunk)}", "data: [DONE]"]
    fake_response = _FakeResponse(lines)
    reverse_map = {
        "tool_ddgs_search": "tool:ddgs.search",
        "tool_news_search": "tool:news.search",
    }

    frames: list[tuple[str | None, dict]] = []
    current_event: str | None = None
    async for line in _chat_stream_as_responses(fake_response, reverse_map):
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split("event: ", 1)[1]
        elif line.startswith("data:") and current_event:
            payload = json.loads(line.split("data: ", 1)[1])
            frames.append((current_event, payload))

    deltas = [payload for event, payload in frames if event == "response.function_call_arguments.delta"]
    assert len(deltas) == 2
    assert deltas[0]["index"] == 0
    assert deltas[1]["index"] == 1
    assert deltas[0]["function_name"] == "tool_ddgs_search"
    assert deltas[1]["function_name"] == "tool_news_search"
    assert deltas[0]["name"] == "tool:ddgs.search"
    assert deltas[1]["name"] == "tool:news.search"
    assert deltas[0]["tool_call_id"] == "call_0"
    assert deltas[1]["tool_call_id"] == "call_1"

    done_events = [payload for event, payload in frames if event == "response.function_call_arguments.done"]
    assert len(done_events) == 2
    assert done_events[0]["index"] == 0
    assert done_events[1]["index"] == 1

    completed_events = [payload for event, payload in frames if event == "response.completed"]
    assert completed_events, "response.completed not found in stream"
    tool_calls = completed_events[-1]["tool_calls"]
    assert tool_calls[0]["function_name"] == "tool_ddgs_search"
    assert tool_calls[1]["function_name"] == "tool_news_search"
    assert tool_calls[0]["name"] == "tool:ddgs.search"
    assert tool_calls[1]["name"] == "tool:news.search"
    assert tool_calls[0]["index"] == 0
    assert tool_calls[1]["index"] == 1
