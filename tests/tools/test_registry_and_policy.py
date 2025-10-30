import copy

import pytest

from app.runtime.agents.codeless import invoke_llm
from app.tools import ArgMode, clear_tools, filter_schema_visible, register_tool
from app.tools.policy import ToolPolicy, can_use_tools
from app.tools.registry import list_tools
from app.tools.types import ToolSpec


@pytest.fixture(autouse=True)
def _reset_tools() -> None:
    clear_tools()
    from app.tools.builtin import register_all

    register_all()
    yield
    clear_tools()
    register_all()


def test_register_tool_and_list() -> None:
    spec: ToolSpec = {
        "id": "tool:test",
        "name": "Test Tool",
        "args_schema": {"type": "object", "properties": {}},
        "arg_behaviors": {},
        "timeout_ms": 1000,
    }
    register_tool(spec)
    all_tools = list_tools()
    assert "tool:test" in all_tools
    assert all_tools["tool:test"]["name"] == "Test Tool"


def test_filter_schema_visible_excludes_llm_hidden() -> None:
    spec: ToolSpec = {
        "id": "tool:policy-search",
        "name": "Policy Search",
        "args_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "apiKey": {"type": "string"},
                "topN": {"type": "number"},
            },
            "required": ["query", "apiKey"],
        },
        "arg_behaviors": {
            "apiKey": {"mode": ArgMode.LLM_HIDDEN, "default": "secret"},
            "topN": {"mode": ArgMode.AGENT_OVERRIDE, "default": 5},
        },
    }

    filtered = filter_schema_visible(spec)

    assert "apiKey" not in filtered["properties"]
    assert filtered.get("required") == ["query"]
    assert "topN" in filtered["properties"]


def test_can_use_tools_respects_policy_modes() -> None:
    assert not can_use_tools(None)
    assert not can_use_tools("Disabled")
    assert can_use_tools(ToolPolicy.AUTO)
    assert can_use_tools("AlwaysAsk")
    assert can_use_tools("Heuristic")
    assert not can_use_tools("InvalidPolicy")


@pytest.mark.anyio
async def test_max_calls_per_turn_limits_tool_invocations(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded_requests: list[dict] = []

    async def _fake_response(body, *, context_headers, client=None, max_attempts=None):
        recorded_requests.append(copy.deepcopy(body))
        if len(recorded_requests) == 1:
            return {
                "output": [
                    {
                        "content": [
                            {
                                "type": "tool_call",
                                "id": "call-1",
                                "name": "tool:policy-test",
                                "arguments": {"query": "hi"},
                            }
                        ]
                    }
                ],
                "usage": {},
            }
        return {"output_text": "done", "usage": {"output_tokens": 1}}

    monkeypatch.setattr("app.runtime.agents.codeless.create_response", _fake_response)

    captured: dict[str, dict] = {}

    async def _handler(args):
        captured["args"] = args
        return {"ok": True}

    spec: ToolSpec = {
        "id": "tool:policy-test",
        "name": "Policy Tool",
        "args_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "arg_behaviors": {},
        "handler": _handler,
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
                "maxCallsPerTurn": 1,
                "parallelism": 1,
                "attached": [],
            },
        },
    }

    await invoke_llm({"messages": [{"role": "user", "content": "hi"}]}, agent, "Prompt", attached_tools=[spec])

    assert captured["args"]["query"] == "hi"
    assert len(recorded_requests) == 2
    assert recorded_requests[1]["input"][-1]["role"] == "tool"
