import json

from app.http.openai_client import _chat_response_to_responses
from app.providers.openai_like.payloads import build_chat_messages


def test_build_chat_messages_sanitizes_tool_names():
    tools = [
        {
            "id": "tool:ddgs.search",
            "description": "DuckDuckGo Search",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]

    payload = build_chat_messages(
        turns=[],
        model="gpt-4o",
        stream=True,
        tools=tools,
        tool_choice="auto",
    )

    assert "tools" in payload
    tool_entry = payload["tools"][0]
    assert tool_entry["type"] == "function"
    function_block = tool_entry["function"]
    assert function_block["name"] == "tool_ddgs_search"
    assert ":" not in function_block["name"]
    assert "." not in function_block["name"]
    assert len(function_block["name"]) <= 64
    assert function_block["parameters"]["type"] == "object"

    reverse_map = payload.get("_tool_name_reverse_map")
    assert reverse_map == {"tool_ddgs_search": "tool:ddgs.search"}

    # Simulate the body we send to OpenAI and ensure the reverse map is dropped.
    serialized = json.dumps({k: v for k, v in payload.items() if not k.startswith("_")})
    assert "_tool_name_reverse_map" not in serialized

    assert payload["tool_choice"] == "auto"


def test_chat_response_normalizes_tool_names_with_reverse_map():
    reverse_map = {"tool_ddgs_search": "tool:ddgs.search"}
    raw_response = {
        "id": "chatcmpl-123",
        "model": "gpt-4o",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_tool_1",
                            "type": "function",
                            "function": {
                                "name": "tool_ddgs_search",
                                "arguments": '{"query":"chips"}',
                            },
                        }
                    ],
                }
            }
        ],
    }

    normalized = _chat_response_to_responses(raw_response, reverse_map)
    assert normalized["tool_calls"][0]["name"] == "tool:ddgs.search"
    assert normalized["tool_calls"][0]["arguments"] == '{"query":"chips"}'
    assert normalized["tool_calls"][0]["id"] == "call_tool_1"


def test_build_chat_messages_deduplicates_sanitized_names():
    tools = [
        {"id": "tool:example.run", "description": "Example runner"},
        {"id": "tool_example:run", "description": "Similar name"},
    ]

    payload = build_chat_messages(
        turns=[],
        model="gpt-4o",
        stream=False,
        tools=tools,
    )

    names = [entry["function"]["name"] for entry in payload["tools"]]
    assert len(set(names)) == len(names)

    reverse_map = payload["_tool_name_reverse_map"]
    assert reverse_map[names[0]] == "tool:example.run"
    assert reverse_map[names[1]] == "tool_example:run"
