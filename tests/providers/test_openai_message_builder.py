import json

from app.providers.openai_like.payloads import build_chat_messages


def test_dedup_system_and_clean_user():
    payload = build_chat_messages(
        [
            {"role": "system", "content": "Keep calm"},
            {"role": "system", "content": "Keep calm"},
            {"role": "user", "content": "text: latest ukraine news"},
        ],
        model="gpt-4o",
        stream=False,
    )

    messages = payload["messages"]
    assert messages[0] == {"role": "system", "content": "Keep calm"}
    assert messages[1] == {"role": "user", "content": "latest ukraine news"}
    assert len(messages) == 2


def test_tool_choreography_single():
    payload = build_chat_messages(
        [
            {"role": "system", "content": "Org preamble"},
            {"role": "user", "content": "latest ukraine news"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_tool_ddgs_search_1",
                        "function": {"name": "tool_ddgs_search", "arguments": {"query": "news"}},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_tool_ddgs_search_1",
                "name": "tool_ddgs_search",
                "content": '{"results": []}',
            },
        ],
        model="gpt-4o",
        stream=False,
    )

    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    assistant = messages[2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"]
    call = assistant["tool_calls"][0]
    assert call["id"] == "call_tool_ddgs_search_1"
    assert call["function"]["name"] == "tool_ddgs_search"
    args = json.loads(call["function"]["arguments"])
    assert args == {"query": "news"}

    tool_message = messages[3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_tool_ddgs_search_1"


def test_parallel_tool_calls():
    payload = build_chat_messages(
        [
            {"role": "system", "content": "Org preamble"},
            {"role": "user", "content": "summarize and fetch"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "function": {"name": "weather_tool", "arguments": {"city": "London"}},
                    },
                    {
                        "id": "call_search",
                        "function": {"name": "search_tool", "arguments": {"query": "news"}},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_weather", "name": "weather_tool", "content": "sunny"},
            {"role": "tool", "tool_call_id": "call_search", "name": "search_tool", "content": "results"},
        ],
        model="gpt-4o",
        stream=False,
    )

    messages = payload["messages"]
    assistant = messages[2]
    assert len(assistant["tool_calls"]) == 2
    ids = {call["id"] for call in assistant["tool_calls"]}
    assert ids == {"call_weather", "call_search"}

    tool_ids = [msg["tool_call_id"] for msg in messages if msg["role"] == "tool"]
    assert tool_ids == ["call_weather", "call_search"]


def test_no_tools_param_when_none():
    payload = build_chat_messages(
        [{"role": "user", "content": "hello"}],
        model="gpt-4o",
        stream=False,
        tools=None,
    )

    assert "tools" not in payload
