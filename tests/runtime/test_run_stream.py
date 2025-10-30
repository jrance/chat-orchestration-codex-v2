import copy

import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.agents import codeless as codeless_mod
from app.runtime.agents.codeless import LLMResult
from app.runtime.engine import ExecutionResult, run_once, run_stream
from app.telemetry.models import TelemetryLevel


def _headers() -> ExecutionHeaders:
    return ExecutionHeaders(
        tenant_id="tenant-1",
        telemetry=TelemetryLevel.NONE,
        correlation_id="corr",
        request_id="req",
        timestamp="2024-01-01T00:00:00Z",
        client_id=None,
        extra_headers={},
    )


def _sample_ir() -> dict:
    return {
        "meta": {"id": "pkg", "name": "Test", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Agent",
                "data": {
                    "systemInstructions": "Be helpful",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0.1, "topP": 1, "maxTokens": 64, "stop": []},
                    "context": {"historyWindow": {"mode": "LastN", "n": 5}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch):
    async def _invoke_stub(state, agent_node, prompt):
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        messages.append({"role": "assistant", "content": f"stub:{prompt[:5]}"})
        new_state["messages"] = messages
        return LLMResult(
            state=new_state,
            response={"output_text": "stub response"},
            output_text="stub response",
            usage={"output_tokens": 2},
        )

    async def _stream_stub(state, agent_node, prompt):
        yield {"type": "response.created", "status": "in_progress"}
        yield {"type": "response.output_text.delta", "delta": "stub "}
        yield {
            "type": "response.completed",
            "output_text": "stub response",
            "usage": {"output_tokens": 2},
        }

    monkeypatch.setattr(codeless_mod, "invoke_llm", _invoke_stub)
    monkeypatch.setattr("app.runtime.engine.stream_codeless", _stream_stub)
    yield


@pytest.mark.anyio
async def test_run_stream_emits_expected_events():
    context = ExecutionContext(headers=_headers())
    events = []
    async for event in run_stream(_sample_ir(), "hello runtime", context, None):
        events.append(event)

    assert events[0].event == "response.created"
    assert any(evt.event == "response.output_text.delta" for evt in events)
    assert events[-1].event == "response.completed"
    assert events[-1].data["output_text"].startswith("stub")


@pytest.mark.anyio
async def test_run_once_returns_execution_result():
    context = ExecutionContext(headers=_headers())
    result: ExecutionResult = await run_once(_sample_ir(), "hello sync", context, None)

    assert result.run_id
    assert result.thread_id
    assert result.output_text.startswith("stub")
    assert result.usage["output_tokens"] == 2
