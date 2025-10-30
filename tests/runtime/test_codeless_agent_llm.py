import copy

import pytest

from app.api.deps import ExecutionContext, ExecutionHeaders
from app.runtime.agents.codeless import LLMResult, invoke_llm
from app.runtime.context import RuntimeContext, reset_runtime_context, set_runtime_context
from app.telemetry.models import TelemetryLevel


def _context() -> ExecutionContext:
    headers = ExecutionHeaders(
        tenant_id="tenant-1",
        telemetry=TelemetryLevel.NONE,
        correlation_id="corr",
        request_id="req",
        timestamp="2024-01-01T00:00:00Z",
        client_id=None,
        extra_headers={},
    )
    ctx = ExecutionContext(headers=headers)
    ctx.ensure_run_ids()
    return ctx


def _agent_node(structured: bool = False) -> dict:
    data = {
        "systemInstructions": "Assist clearly.",
        "model": {"provider": "openai", "modelId": "gpt-4o"},
        "context": {"historyWindow": {"mode": "LastN", "n": 2}},
        "tools": {"policy": "Disabled", "attached": []},
    }
    if structured:
        data["structuredOutput"] = {
            "enabled": True,
            "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "maxRepairAttempts": 1,
        }
    return {"id": "agent", "kind": "agent.codeless", "data": data}


@pytest.mark.anyio
async def test_invoke_llm_appends_message(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict = {}

    async def _fake_create_response(body, *, context_headers, client=None, max_attempts=None):
        recorded["body"] = copy.deepcopy(body)
        recorded["headers"] = dict(context_headers)
        return {"output_text": "hello there", "usage": {"output_tokens": 4}}

    monkeypatch.setattr("app.runtime.agents.codeless.create_response", _fake_create_response)

    ctx = _context()
    token = set_runtime_context(RuntimeContext(execution=ctx, telemetry=None))
    try:
        result: LLMResult = await invoke_llm({"messages": [{"role": "user", "content": "hi"}]}, _agent_node(), "Prompt")
    finally:
        reset_runtime_context(token)

    assert result.output_text == "hello there"
    assert result.state["messages"][-1]["role"] == "assistant"
    assert recorded["headers"]["X-Tenant-Id"] == "tenant-1"
    assert recorded["body"]["input"][0]["role"] == "system"


@pytest.mark.anyio
async def test_invoke_llm_retries_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    async def _sequenced_response(body, *, context_headers, client=None, max_attempts=None):
        attempts.append(1)
        if len(attempts) == 1:
            return {"output_text": "{bad json", "usage": {}}
        return {"output_text": '{"answer":"ok"}', "usage": {"output_tokens": 2}}

    monkeypatch.setattr("app.runtime.agents.codeless.create_response", _sequenced_response)

    ctx = _context()
    token = set_runtime_context(RuntimeContext(execution=ctx, telemetry=None))
    try:
        result: LLMResult = await invoke_llm(
            {"messages": [{"role": "user", "content": "hi"}]},
            _agent_node(structured=True),
            "Prompt",
        )
    finally:
        reset_runtime_context(token)

    assert len(attempts) == 2
    assert result.output_text == '{"answer":"ok"}'
