import copy
import json

import pytest

from app.runtime.agents.codeless import invoke_llm
from app.tools.types import ToolSpec


@pytest.mark.anyio
async def test_tool_call_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded_requests: list[dict] = []

    async def _fake_response(body, *, context_headers=None, client=None, max_attempts=None):
        recorded_requests.append(copy.deepcopy(body))
        if len(recorded_requests) == 1:
            return {
                "output": [
                    {
                        "content": [
                            {
                                "type": "tool_call",
                                "id": "call_tool_ddgs_search_1",
                                "name": "tool_ddgs_search",
                                "arguments": {"query": "latest ukraine news"},
                            }
                        ]
                    }
                ],
                "usage": {},
            }
        return {"output_text": "done", "usage": {"output_tokens": 1}}

    async def _fake_invoke_tool(tool_id, call_args, *, per_call_timeout_ms=None):
        return {"results": ["sample"]}

    monkeypatch.setattr("app.runtime.agents.codeless.create_response", _fake_response)
    monkeypatch.setattr("app.runtime.agents.codeless.invoke_tool", _fake_invoke_tool)

    tool_spec: ToolSpec = {
        "id": "tool_ddgs_search",
        "name": "DuckDuckGo Search",
        "description": "Fetch latest news results.",
        "args_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "handler": _fake_invoke_tool,
    }

    agent = {
        "id": "agent",
        "kind": "agent.codeless",
        "data": {
            "systemInstructions": "",
            "model": {"modelId": "gpt-4o"},
            "tools": {
                "policy": "Auto",
                "timeoutMs": 1000,
                "maxCallsPerTurn": 4,
                "parallelism": 1,
                "attached": [],
            },
        },
    }

    state = {"messages": [{"role": "user", "content": "latest ukraine news"}]}

    result = await invoke_llm(state, agent, "Org preamble", attached_tools=[tool_spec])

    assert len(recorded_requests) == 2

    second_request = recorded_requests[1]
    assert second_request["model"] == "gpt-4o"
    assert second_request["tool_choice"] == "auto"
    assert second_request["tools"][0]["name"] == "tool_ddgs_search"

    messages = second_request["input"]
    assert [msg["role"] for msg in messages] == ["system", "user", "assistant", "tool"]

    assistant = messages[2]
    call_entry = assistant["tool_calls"][0]
    assert call_entry["id"] == "call_tool_ddgs_search_1"
    assert call_entry["function"]["name"] == "tool_ddgs_search"
    assert json.loads(call_entry["function"]["arguments"]) == {"query": "latest ukraine news"}

    tool_message = messages[3]
    assert tool_message["tool_call_id"] == "call_tool_ddgs_search_1"
    assert tool_message["name"] == "tool_ddgs_search"
    assert tool_message["content"] == json.dumps({"results": ["sample"]}, ensure_ascii=False)

    tool_results = result.response.get("tool_results", [])
    assert tool_results and tool_results[0]["id"] == "call_tool_ddgs_search_1"
