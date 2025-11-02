import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders, build_telemetry_context
from app.runtime.agents import codeless as codeless_mod
from app.runtime.engine import run_stream


def _headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-sse",
        telemetry_override=False,
        telemetry_redaction_override=False,
    )


def _ir() -> dict:
    return {
        "meta": {"id": "pkg-sse", "name": "ToolCallStream", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Use tools.",
                    "model": {"provider": "openai", "modelId": "gpt-4o"},
                    "context": {"historyWindow": {"mode": "LastN", "n": 3}},
                    "tools": {"policy": "Auto", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


@pytest.mark.anyio
async def test_canonical_tool_argument_events(monkeypatch: pytest.MonkeyPatch):
    tool_runtime = codeless_mod.ToolRuntime(
        specs={"tool:web.search": {"id": "tool:web.search"}},
        payload=[
            {"type": "function", "name": "tool:web.search", "description": "", "parameters": {"type": "object", "properties": {}}}
        ],
        policy="Auto",
        max_calls=None,
        timeout_ms=None,
        parallelism=1,
        redact=False,
        enabled=True,
        mcp_servers={},
        name_reverse={"tool_web_search": "tool:web.search"},
        sanitized_names={"tool:web.search": "tool_web_search"},
    )

    monkeypatch.setattr(codeless_mod, "_prepare_tool_runtime", lambda *args, **kwargs: tool_runtime)
    monkeypatch.setattr(codeless_mod, "_execute_tool_call", lambda call, *_args, **_kwargs: {"id": call["id"], "name": call["name"], "output": {}})

    fragments = ['{"query":"latest', ' news"}']

    async def _stub_stream(state, agent_node, prompt, **_kwargs):
        yield {"type": "response.created"}
        for piece in fragments:
            yield {
                "type": "response.tool_call.arguments.delta",
                "tool_call_id": "call_1",
                "name": "tool:web.search",
                "function_name": "tool_web_search",
                "arguments": piece,
                "index": 0,
            }
        yield {
            "type": "response.tool_call.arguments.done",
            "tool_call_id": "call_1",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "index": 0,
        }
        yield {
            "type": "response.completed",
            "output_text": "",
            "usage": {"output_tokens": 0},
        }

    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stub_stream)

    context = ExecutionContext(headers=_headers())
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(context.headers)

    events = []
    async for evt in run_stream(_ir(), "search", context, None):
        events.append(evt)

    arg_events = [evt for evt in events if evt.event.startswith("response.tool_call.arguments")]
    assert arg_events, "Expected tool call argument events"
    assert all(evt.event.startswith("response.tool_call.arguments") for evt in arg_events)

    done_event = next(evt for evt in arg_events if evt.event.endswith(".done"))
    assert done_event.data["arguments"] == "".join(fragments)

    completed = next(evt for evt in events if evt.event == "response.completed")
    tool_calls = completed.data.get("tool_calls", [])
    assert tool_calls
    assert tool_calls[0]["arguments"] == "".join(fragments)
