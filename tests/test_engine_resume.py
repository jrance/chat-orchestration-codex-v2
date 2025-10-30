import copy

import pytest

from app.api.deps import ExecutionContext
from app.api.models import ExecutionHeaders
from app.compiler import registry
from app.runtime.agents import codeless as codeless_mod
from app.runtime.agents.codeless import LLMResult
from app.runtime.engine import resume_run, run_once
from app.runtime.state.checkpointer import get_checkpointer, reset_checkpointer
from app.runtime.state.models import RunStatus
from app.runtime.state_store import RunStateRecord, get_run_state_store, reset_run_state_store


def _router_ir() -> dict:
    return {
        "meta": {"id": "hitl", "name": "HITL Flow", "version": "1.0.0"},
        "nodes": [
            {
                "id": "router",
                "kind": "router",
                "label": "Supervisor",
                "data": {
                    "prompt": "Route to <targets>.",
                    "minConfidence": 0.9,
                    "tieBreak": "HighestConfidence",
                    "fallback": {"mode": "AskUserClarify", "prompt": "Choose an agent."},
                    "allowBelowMinForTieBreak": False,
                    "routeSchema": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["target"],
                    },
                    "targets": ["Policy Agent", "Benefits Agent"],
                    "autoSyncEnum": True,
                },
            },
            {
                "id": "policy_agent",
                "kind": "agent.codeless",
                "label": "Policy Agent",
                "data": {
                    "systemInstructions": "Policy helper.",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0.1, "topP": 1, "maxTokens": 32},
                    "context": {"historyWindow": {"mode": "LastN", "n": 2}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            },
            {
                "id": "benefits_agent",
                "kind": "agent.codeless",
                "label": "Benefits Agent",
                "data": {
                    "systemInstructions": "Benefits helper.",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0.1, "topP": 1, "maxTokens": 32},
                    "context": {"historyWindow": {"mode": "LastN", "n": 2}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            },
        ],
        "edges": [
            {"id": "e1", "from": "router", "to": "policy_agent"},
            {"id": "e2", "from": "router", "to": "benefits_agent"},
        ],
        "entryId": "router",
    }


def _simple_agent_ir() -> dict:
    return {
        "meta": {"id": "agent", "name": "Simple Agent", "version": "1.0.0"},
        "nodes": [
            {
                "id": "agent",
                "kind": "agent.codeless",
                "label": "Assistant",
                "data": {
                    "systemInstructions": "Keep responses brief.",
                    "model": {"provider": "openai", "modelId": "gpt-4o", "temperature": 0.1, "topP": 1, "maxTokens": 32},
                    "context": {"historyWindow": {"mode": "LastN", "n": 2}},
                    "tools": {"policy": "Disabled", "attached": []},
                },
            }
        ],
        "edges": [],
        "entryId": "agent",
    }


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    reset_checkpointer()
    reset_run_state_store()
    registry.clear()
    yield
    reset_checkpointer()
    reset_run_state_store()
    registry.clear()


@pytest.fixture
def _stub_llm(monkeypatch: pytest.MonkeyPatch):
    async def _invoke_stub(state, agent_node, prompt, **_kwargs):
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        messages.append({"role": "assistant", "content": "stub-response"})
        new_state["messages"] = messages
        return LLMResult(
            state=new_state,
            response={"output_text": "stub-response"},
            output_text="stub-response",
            usage={"output_tokens": 2},
        )

    async def _fake_router_llm(*_args, **_kwargs):
        # Force AskUserClarify fallback by returning low confidence.
        return {}, {"output_text": '{"target": "Unknown", "confidence": 0.2}'}

    monkeypatch.setattr(codeless_mod, "invoke_llm", _invoke_stub)
    monkeypatch.setattr("app.runtime.patterns.router.run_router_llm", _fake_router_llm)
    yield


@pytest.mark.anyio
async def test_resume_run_missing_run():
    with pytest.raises(ValueError) as exc:
        await resume_run(
            run_id="missing",
            resume_kind="continue",
            payload={},
            tenant_id="tenant-1",
            request_id=None,
            correlation_id=None,
        )
    assert str(exc.value) == "RUN_NOT_FOUND"


@pytest.mark.anyio
async def test_resume_router_choice_completes(_stub_llm: None):
    headers = ExecutionHeaders(tenant_id="tenant-1")
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()

    result = await run_once(_router_ir(), "Need help with policy.", context, telemetry=None)
    assert result.response.get("status") == "paused"

    resumed = await resume_run(
        run_id=result.run_id,
        resume_kind="router_choice",
        payload={"choice": {"target": "Policy Agent"}},
        tenant_id=headers.tenant_id or "",
        request_id=headers.request_id,
        correlation_id=headers.correlation_id,
    )

    assert resumed["runId"] == result.run_id
    assert resumed["status"] == "completed"
    assert "pause" not in resumed or resumed["pause"] is None


@pytest.mark.anyio
async def test_resume_user_message_appends_input(_stub_llm: None):
    headers = ExecutionHeaders(tenant_id="tenant-1")
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()

    initial = await run_once(_simple_agent_ir(), "Hello there", context, telemetry=None)
    run_id = initial.run_id

    store = get_run_state_store()
    record = store.get_state(run_id)
    assert record is not None

    paused_state = copy.deepcopy(record.state)
    paused_state["status"] = "paused"
    paused_metadata = copy.deepcopy(record.metadata)
    paused_metadata["status"] = "paused"
    if "messages" not in paused_state:
        paused_state["messages"] = []

    store.put_state(
        RunStateRecord(
            run_id=run_id,
            state=paused_state,
            metadata=paused_metadata,
        )
    )

    manager = get_checkpointer()
    manager.mark_status(run_id, RunStatus.PAUSED, metadata=paused_metadata)

    additional_message = "Providing more context."
    resumed = await resume_run(
        run_id=run_id,
        resume_kind="user_message",
        payload={"message": additional_message},
        tenant_id=headers.tenant_id or "",
        request_id=headers.request_id,
        correlation_id=headers.correlation_id,
    )

    assert resumed["runId"] == run_id
    assert resumed["status"] in {"running", "completed"}

    resumed_record = store.get_state(run_id)
    assert resumed_record is not None
    messages = [
        msg.get("content")
        for msg in resumed_record.state.get("messages", [])
        if isinstance(msg, dict)
    ]
    assert additional_message in messages
    assert resumed_record.metadata.get("status") != "paused"


@pytest.mark.anyio
async def test_resume_continue_clears_pause(_stub_llm: None):
    headers = ExecutionHeaders(tenant_id="tenant-1")
    context = ExecutionContext(headers=headers)
    context.ensure_run_ids()

    paused_result = await run_once(_router_ir(), "Need help", context, telemetry=None)
    assert paused_result.response.get("status") == "paused"

    resumed = await resume_run(
        run_id=paused_result.run_id,
        resume_kind="continue",
        payload={},
        tenant_id=headers.tenant_id or "",
        request_id=headers.request_id,
        correlation_id=headers.correlation_id,
    )

    assert resumed["status"] in {"running", "completed"}
    store = get_run_state_store()
    record = store.get_state(paused_result.run_id)
    assert record is not None
    assert record.metadata.get("status") != "paused"
