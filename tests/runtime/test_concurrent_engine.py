import copy

import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.agents.codeless import LLMResult
from app.runtime.engine import run_once, run_stream
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


def _concurrent_ir(strategy: str = "HighestScore") -> dict:
    return {
        "meta": {"id": "pkg", "name": "Test", "version": "1.0.0"},
        "nodes": [
            {
                "id": "concurrent",
                "kind": "concurrent",
                "label": "Concurrent",
                "data": {"merge": {"strategy": strategy}},
            },
            {
                "id": "alpha",
                "kind": "agent.codeless",
                "label": "Alpha Agent",
                "data": {
                    "systemInstructions": "Be helpful",
                    "styleGuide": "",
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o",
                        "temperature": 0.1,
                        "topP": 1,
                        "maxTokens": 64,
                        "stop": [],
                    },
                    "context": {"historyWindow": {"mode": "LastN", "n": 5}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            },
            {
                "id": "beta",
                "kind": "agent.codeless",
                "label": "Beta Agent",
                "data": {
                    "systemInstructions": "Be helpful",
                    "styleGuide": "",
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o",
                        "temperature": 0.1,
                        "topP": 1,
                        "maxTokens": 64,
                        "stop": [],
                    },
                    "context": {"historyWindow": {"mode": "LastN", "n": 5}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            },
        ],
        "edges": [
            {"id": "e1", "from": "concurrent", "to": "alpha"},
            {"id": "e2", "from": "concurrent", "to": "beta"},
        ],
        "entryId": "concurrent",
    }


@pytest.fixture(autouse=True)
def _stub_invoke_llm(monkeypatch: pytest.MonkeyPatch):
    confidence_map = {"alpha": 0.25, "beta": 0.9}

    async def _fake_invoke_llm(state, agent_node, prompt, **_kwargs):
        del prompt  # unused
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        output = f"{agent_node.get('label')} response"
        messages.append({"role": "assistant", "content": output})
        new_state["messages"] = messages
        usage = {"output_tokens": len(output.split())}
        response = {"confidence": confidence_map.get(agent_node["id"], 0.0)}
        return LLMResult(state=new_state, response=response, output_text=output, usage=usage)

    monkeypatch.setattr("app.runtime.agents.codeless.invoke_llm", _fake_invoke_llm)
    yield


@pytest.mark.anyio
async def test_run_once_concurrent_node_selects_highest_confidence():
    context = ExecutionContext(headers=_headers())
    result = await run_once(_concurrent_ir(), "question", context, None)

    assert "Beta Agent" in result.output_text
    assert result.response["strategy"] == "HighestScore"
    assert result.response["chosen"]["nodeId"] == "beta"
    assert result.usage["output_tokens"] == len(result.output_text.split())


@pytest.mark.anyio
async def test_run_stream_concurrent_node_emits_final_output():
    context = ExecutionContext(headers=_headers())
    events = []
    async for event in run_stream(_concurrent_ir(), "question", context, None):
        events.append(event)

    assert events[0].event == "response.created"
    assert events[-1].event == "response.completed"
    completed = events[-1].data
    assert "Beta Agent" in completed["output_text"]
    assert completed["response"]["metadata"]["concurrent"]["chosenNodeId"] == "beta"

