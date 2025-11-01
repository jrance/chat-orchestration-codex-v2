import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.agents import codeless as codeless_mod
from app.runtime.engine import run_stream
from app.telemetry.models import TelemetryLevel


def _headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-tools",
        telemetry=TelemetryLevel.NONE,
        correlation_id="corr",
        request_id="req",
        timestamp="2024-01-01T00:00:00Z",
        client_id=None,
        extra_headers={},
    )


def _tool_ir() -> dict:
    return {
        "meta": {"id": "pkg-tools", "name": "FragmentedToolCalls", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Use tools when available.",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0, "topP": 1, "maxTokens": 64, "stop": []},
                    "context": {"historyWindow": {"mode": "LastN", "n": 5}},
                    "tools": {"policy": "Auto", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


@pytest.mark.anyio
async def test_fragmented_tool_arguments_are_buffered(monkeypatch: pytest.MonkeyPatch):
    tool_runtime = codeless_mod.ToolRuntime(
        specs={"tool:ddgs.search": {"id": "tool:ddgs.search"}},
        payload=[
            {
                "type": "function",
                "name": "tool:ddgs.search",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        policy="Auto",
        max_calls=None,
        timeout_ms=None,
        parallelism=1,
        redact=False,
        enabled=True,
        mcp_servers={},
        name_reverse={"tool_ddgs_search": "tool:ddgs.search"},
        sanitized_names={"tool:ddgs.search": "tool_ddgs_search"},
    )

    monkeypatch.setattr(codeless_mod, "_prepare_tool_runtime", lambda *args, **kwargs: tool_runtime)

    executed_calls: list[dict] = []

    async def _fake_execute(call, runtime, telemetry, *, index=None):
        executed_calls.append({"call": call, "index": index})
        return {
            "id": call.get("id"),
            "name": call.get("name"),
            "output": {"value": "ok"},
        }

    monkeypatch.setattr(codeless_mod, "_execute_tool_call", _fake_execute)

    fragments = [
        '{"',
        "query",
        '":"',
        "latest news on Ukraine",
        '","vertical":"',
        "news",
        '"}',
    ]

    async def _tool_phase():
        yield {"type": "response.created"}
        for piece in fragments:
            yield {
                "type": "response.function_call_arguments.delta",
                "tool_call_id": "call_X",
                "name": "tool:ddgs.search",
                "function_name": "tool_ddgs_search",
                "arguments": piece,
                "index": 0,
            }
        yield {
            "type": "response.function_call_arguments.done",
            "tool_call_id": "call_X",
            "name": "tool:ddgs.search",
            "function_name": "tool_ddgs_search",
            "index": 0,
            "arguments": "".join(fragments),
        }
        yield {
            "type": "response.completed",
            "output_text": "",
            "usage": {"output_tokens": 0},
            "tool_calls": [
                {
                    "id": "call_X",
                    "name": "tool:ddgs.search",
                    "function_name": "tool_ddgs_search",
                    "index": 0,
                    "arguments": "".join(fragments),
                }
            ],
            "finish_reason": "tool_calls",
            "response": {"finish_reason": "tool_calls"},
        }

    async def _final_phase():
        yield {"type": "response.created"}
        yield {"type": "response.output_text.delta", "delta": "final answer"}
        yield {"type": "response.output_text.done"}
        yield {
            "type": "response.completed",
            "output_text": "final answer",
            "usage": {"output_tokens": 4},
        }

    call_counter = {"count": 0}

    async def _stream_wrapper(state, agent_node, prompt, **kwargs):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            async for event in _tool_phase():
                yield event
        else:
            async for event in _final_phase():
                yield event

    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream_wrapper)

    context = ExecutionContext(headers=_headers())
    events = []
    async for evt in run_stream(_tool_ir(), "search query", context, None):
        events.append(evt)

    assert executed_calls, "tool call should execute exactly once"
    assert executed_calls[0]["call"]["name"] == "tool:ddgs.search"
    assert executed_calls[0]["call"]["arguments"] == {
        "query": "latest news on Ukraine",
        "vertical": "news",
    }

    created_events = [evt for evt in events if evt.event == "response.tool_call.created"]
    assert len(created_events) == 1
    assert created_events[0].data["tool_call_id"] == "call_X"

    result_done_events = [evt for evt in events if evt.event == "response.tool_result.done"]
    assert len(result_done_events) == 1
    assert result_done_events[0].data["tool_call_id"] == "call_X"

    delta_events = [
        evt for evt in events if evt.event == "response.tool_call.delta" and evt.data.get("status") != "running"
    ]
    assert [evt.data.get("status") for evt in delta_events] == ["completed"]

    assert events[-1].event == "response.completed"
    assert events[-1].data["output_text"] == "final answer"
