import asyncio

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
        "meta": {"id": "pkg-tools", "name": "ParallelTools", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Use tools when helpful.",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0, "topP": 1, "maxTokens": 64, "stop": []},
                    "context": {"historyWindow": {"mode": "LastN", "n": 5}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


@pytest.mark.anyio
async def test_parallel_tool_execution_order(monkeypatch: pytest.MonkeyPatch):
    tool_runtime = codeless_mod.ToolRuntime(
        specs={
            "fast_tool": {"id": "fast_tool"},
            "slow_tool": {"id": "slow_tool"},
        },
        payload=[
            {"type": "function", "name": "fast_tool", "description": "", "parameters": {"type": "object", "properties": {}}},
            {"type": "function", "name": "slow_tool", "description": "", "parameters": {"type": "object", "properties": {}}},
        ],
        policy="Auto",
        max_calls=None,
        timeout_ms=None,
        parallelism=2,
        redact=False,
        enabled=True,
        mcp_servers={},
        name_reverse={"fast_tool_fn": "fast_tool", "slow_tool_fn": "slow_tool"},
        sanitized_names={"fast_tool": "fast_tool_fn", "slow_tool": "slow_tool_fn"},
    )

    monkeypatch.setattr(
        codeless_mod,
        "_prepare_tool_runtime",
        lambda *args, **kwargs: tool_runtime,
    )

    async def _fake_execute(call, runtime, telemetry, *, index=None):
        call_id = call.get("id")
        if call_id == "call_fast":
            await asyncio.sleep(0.01)
            result_payload = {"value": "fast"}
        else:
            await asyncio.sleep(0.05)
            result_payload = {"value": "slow"}
        return {
            "id": call_id,
            "name": call.get("name"),
            "output": result_payload,
        }

    monkeypatch.setattr(codeless_mod, "_execute_tool_call", _fake_execute)

    call_counter = {"count": 0}

    async def _fake_stream(state, agent_node, prompt, **_kwargs):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            yield {"type": "response.created"}
            yield {
                "type": "response.tool_call.arguments.delta",
                "tool_call_id": "call_fast",
                "name": "fast_tool",
                "function_name": "fast_tool_fn",
                "arguments": '{"query": "fast"}',
                "index": 0,
            }
            yield {
                "type": "response.tool_call.arguments.delta",
                "tool_call_id": "call_slow",
                "name": "slow_tool",
                "function_name": "slow_tool_fn",
                "arguments": '{"query": "slow"}',
                "index": 1,
            }
            yield {
                "type": "response.tool_call.arguments.done",
                "tool_call_id": "call_fast",
                "name": "fast_tool",
                "function_name": "fast_tool_fn",
                "index": 0,
            }
            yield {
                "type": "response.tool_call.arguments.done",
                "tool_call_id": "call_slow",
                "name": "slow_tool",
                "function_name": "slow_tool_fn",
                "index": 1,
            }
            yield {
                "type": "response.completed",
                "output_text": "",
                "usage": {"output_tokens": 0},
                "tool_calls": [
                    {
                        "id": "call_fast",
                        "name": "fast_tool",
                        "function_name": "fast_tool_fn",
                        "index": 0,
                        "arguments": '{"query": "fast"}',
                    },
                    {
                        "id": "call_slow",
                        "name": "slow_tool",
                        "function_name": "slow_tool_fn",
                        "index": 1,
                        "arguments": '{"query": "slow"}',
                    },
                ],
            }
        else:
            yield {"type": "response.created"}
            yield {
                "type": "response.output_text.delta",
                "delta": "final answer",
            }
            yield {
                "type": "response.output_text.done",
            }
            yield {
                "type": "response.completed",
                "output_text": "final answer",
                "usage": {"output_tokens": 5},
            }

    monkeypatch.setattr("app.runtime.engine.stream_codeless", _fake_stream)

    context = ExecutionContext(headers=_headers())
    events = []
    async for event in run_stream(_tool_ir(), "run parallel tools", context, None):
        events.append(event)

    content_events = [evt for evt in events if not evt.event.startswith("response.telemetry")]

    created_events = [evt for evt in content_events if evt.event == "response.tool_call.created"]
    assert len(created_events) == 2
    assert {evt.data["index"] for evt in created_events} == {0, 1}

    result_done_events = [evt for evt in content_events if evt.event == "response.tool_result.done"]
    assert [evt.data["tool_call_id"] for evt in result_done_events] == ["call_fast", "call_slow"]

    delta_events = [evt for evt in content_events if evt.event == "response.tool_call.delta"]
    statuses = [evt.data.get("status") for evt in delta_events if evt.data.get("status") != "running"]
    assert statuses == ["completed", "completed"]

    final_output_index = next(
        idx for idx, evt in enumerate(content_events) if evt.event == "response.output_text.delta"
    )
    last_tool_event_index = max(
        idx for idx, evt in enumerate(content_events) if evt.event.startswith("response.tool")
    )
    assert final_output_index > last_tool_event_index

    assert content_events[-1].event == "response.completed"
    assert content_events[-1].data["output_text"] == "final answer"
