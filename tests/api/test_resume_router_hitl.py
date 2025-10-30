import copy

import pytest

from httpx import AsyncClient
from app.runtime.agents.codeless import LLMResult


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
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o",
                        "temperature": 0.1,
                        "topP": 1,
                        "maxTokens": 32,
                    },
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
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o",
                        "temperature": 0.1,
                        "topP": 1,
                        "maxTokens": 32,
                    },
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


@pytest.mark.anyio
async def test_resume_router_choice(async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    async def _fake_router_llm(*_args, **_kwargs):
        # Force AskUserClarify fallback by returning low confidence.
        return {}, {"output_text": '{"target": "Unknown", "confidence": 0.2}'}

    async def _invoke_stub(state, agent_node, prompt, **_kwargs):
        new_state = copy.deepcopy(state)
        messages = list(new_state.get("messages") or [])
        messages.append({"role": "assistant", "content": f"stub-{agent_node.get('id')}"})
        new_state["messages"] = messages
        return LLMResult(
            state=new_state,
            response={"output_text": "stub-response", "usage": {"output_tokens": 1}},
            output_text="stub-response",
            usage={"output_tokens": 1},
        )

    monkeypatch.setattr("app.runtime.patterns.router.run_router_llm", _fake_router_llm)
    monkeypatch.setattr("app.runtime.agents.codeless.invoke_llm", _invoke_stub)

    execute_body = {"orchestration": _router_ir(), "input": "Need help with policy."}
    execute_resp = await async_client.post(
        "/v1/execute",
        json=execute_body,
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert execute_resp.status_code == 201
    initial_payload = execute_resp.json()
    assert initial_payload["status"] == "paused"
    assert initial_payload["pause"]["reason"] == "router.ask_user"

    run_id = initial_payload["runId"]
    resume_resp = await async_client.post(
        f"/v1/execute/{run_id}/resume",
        json={"kind": "router_choice", "choice": {"target": "Policy Agent"}},
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert resume_resp.status_code == 200
    resumed_payload = resume_resp.json()
    assert resumed_payload["runId"] == run_id
    assert resumed_payload["status"] == "completed"
    assert "pause" not in resumed_payload





