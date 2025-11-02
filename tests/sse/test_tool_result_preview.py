import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders, build_telemetry_context
from app.api.models import TelemetryLevel
from app.config.settings import settings
from app.runtime.agents import codeless as codeless_mod
from app.runtime.engine import run_stream


def _headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-preview",
        telemetry_override=False,
        telemetry_redaction_override=False,
    )


def _ir() -> dict:
    return {
        "meta": {"id": "pkg-preview", "name": "ToolPreview", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Use search.",
                    "model": {"provider": "openai", "modelId": "gpt-4o"},
                    "context": {"historyWindow": {"mode": "LastN", "n": 3}},
                    "tools": {"policy": "Auto", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


def _tool_runtime(redact: bool) -> codeless_mod.ToolRuntime:
    return codeless_mod.ToolRuntime(
        specs={"tool:web.search": {"id": "tool:web.search"}},
        payload=[
            {"type": "function", "name": "tool:web.search", "description": "", "parameters": {"type": "object", "properties": {}}}
        ],
        policy="Auto",
        max_calls=None,
        timeout_ms=None,
        parallelism=1,
        redact=redact,
        enabled=True,
        mcp_servers={},
        name_reverse={"tool_web_search": "tool:web.search"},
        sanitized_names={"tool:web.search": "tool_web_search"},
    )


@pytest.mark.anyio
async def test_tool_result_delta_emits_preview(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "tool_result_max_chars", 16, raising=False)

    runtime = _tool_runtime(redact=False)
    monkeypatch.setattr(codeless_mod, "_prepare_tool_runtime", lambda *args, **kwargs: runtime)

    async def _execute(call, *_args, **_kwargs):
        return {
            "id": call["id"],
            "name": call["name"],
            "output": {"summary": "Top headline 1\nTop headline 2\nTop headline 3"},
        }

    monkeypatch.setattr(codeless_mod, "_execute_tool_call", _execute)

    async def _stream(state, agent_node, prompt, **_kwargs):
        yield {"type": "response.created"}
        yield {
            "type": "response.tool_call.arguments.delta",
            "tool_call_id": "call_1",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "arguments": '{"query":"latest"}',
            "index": 0,
        }
        yield {
            "type": "response.tool_call.arguments.done",
            "tool_call_id": "call_1",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "index": 0,
            "arguments": '{"query":"latest"}',
        }
        yield {"type": "response.completed", "output_text": "", "usage": {"output_tokens": 0}}

    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream)

    context = ExecutionContext(headers=_headers())
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(context.headers)

    events = []
    async for evt in run_stream(_ir(), "news", context, None):
        events.append(evt)

    delta_events = [evt for evt in events if evt.event == "response.tool_result.delta"]
    assert delta_events, "Expected tool_result.delta events"
    preview_output = delta_events[0].data["output"]
    assert "[truncated" in preview_output or preview_output.endswith("[truncated 0 chars]")

    done_event = next(evt for evt in events if evt.event == "response.tool_result.done")
    assert isinstance(done_event.data.get("output"), dict)


@pytest.mark.anyio
async def test_tool_result_created_contains_preview_when_redacted(monkeypatch: pytest.MonkeyPatch):
    runtime = _tool_runtime(redact=True)
    monkeypatch.setattr(codeless_mod, "_prepare_tool_runtime", lambda *args, **kwargs: runtime)

    async def _execute(call, *_args, **_kwargs):
        return {
            "id": call["id"],
            "name": call["name"],
            "output": {"summary": "secret data"},
        }

    monkeypatch.setattr(codeless_mod, "_execute_tool_call", _execute)

    async def _stream(state, agent_node, prompt, **_kwargs):
        yield {"type": "response.created"}
        yield {
            "type": "response.tool_call.arguments.delta",
            "tool_call_id": "call_1",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "arguments": '{"query":"latest"}',
            "index": 0,
        }
        yield {
            "type": "response.tool_call.arguments.done",
            "tool_call_id": "call_1",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "index": 0,
        }
        yield {"type": "response.completed", "output_text": "", "usage": {"output_tokens": 0}}

    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream)

    context = ExecutionContext(headers=_headers())
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(context.headers)

    events = []
    async for evt in run_stream(_ir(), "news", context, None):
        events.append(evt)

    created_event = next(evt for evt in events if evt.event == "response.tool_result.created")
    preview = created_event.data.get("result_preview")
    assert preview and preview["type"] in {"object", "list", "text"}

    delta_events = [evt for evt in events if evt.event == "response.tool_result.delta"]
    assert not delta_events, "Redacted runs should not emit tool_result.delta"

    done_event = next(evt for evt in events if evt.event == "response.tool_result.done")
    assert done_event.data.get("output") is None


@pytest.mark.anyio
async def test_tool_result_excerpt_present_when_telemetry_verbose(monkeypatch: pytest.MonkeyPatch):
    runtime = _tool_runtime(redact=True)
    monkeypatch.setattr(codeless_mod, "_prepare_tool_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(settings, "telemetry_enabled", True, raising=False)

    async def _execute(call, *_args, **_kwargs):
        return {
            "id": call["id"],
            "name": call["name"],
            "output": "City forecast: rain expected throughout the afternoon with mild winds.",
        }

    monkeypatch.setattr(codeless_mod, "_execute_tool_call", _execute)

    async def _stream(state, agent_node, prompt, **_kwargs):
        yield {"type": "response.created"}
        yield {
            "type": "response.tool_call.arguments.delta",
            "tool_call_id": "call_2",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "arguments": '{"query":"forecast"}',
            "index": 0,
        }
        yield {
            "type": "response.tool_call.arguments.done",
            "tool_call_id": "call_2",
            "name": "tool:web.search",
            "function_name": "tool_web_search",
            "index": 0,
        }
        yield {"type": "response.completed", "output_text": "", "usage": {"output_tokens": 0}}

    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream)

    headers = ExecutionHeaders(
        tenant_id="tenant-preview",
        telemetry=TelemetryLevel.VERBOSE,
        telemetry_override=True,
        telemetry_redaction_override=False,
    )
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()
    context.telemetry = build_telemetry_context(headers)

    events = []
    async for evt in run_stream(_ir(), "forecast", context, None):
        events.append(evt)

    created_event = next(evt for evt in events if evt.event == "response.tool_result.created")
    done_event = next(evt for evt in events if evt.event == "response.tool_result.done")

    assert created_event.data.get("result_excerpt")
    assert done_event.data.get("result_excerpt")
    assert done_event.data.get("output") is None
