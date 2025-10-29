import pytest
from httpx import AsyncClient


def _agent_node(node_id: str = "agent-1") -> dict:
    return {
        "id": node_id,
        "kind": "agent.codeless",
        "label": "Primary Agent",
        "data": {
            "systemInstructions": "Use tools when available.",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.2,
                "topP": 1,
                "maxTokens": 128,
            },
            "context": {"historyWindow": {"mode": "LastN", "n": 3}},
            "tools": {"policy": "Disabled", "attached": ["tool-1"]},
        },
    }


def _tool_node(node_id: str = "tool-1") -> dict:
    return {
        "id": node_id,
        "kind": "tool",
        "label": "Echo Tool",
        "data": {"name": "tool:echo", "argsSchema": {"type": "object"}},
    }


def _base_meta() -> dict:
    return {"id": "pkg-1", "name": "Test Package", "version": "1.0.0"}


@pytest.mark.anyio
async def test_validate_success(async_client: AsyncClient):
    pkg = {
        "meta": _base_meta(),
        "nodes": [_agent_node(), _tool_node()],
        "edges": [{"id": "edge-1", "from": "agent-1", "to": "tool-1"}],
        "entryId": "agent-1",
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert payload["warnings"] == []

    normalized = payload["normalized"]
    assert normalized["entryId"] == "agent-1"
    assert set(normalized["nodeById"].keys()) == {"agent-1", "tool-1"}
    bindings = normalized["resolved"]["agentToolBindings"]
    assert bindings["agent-1"] == ["tool-1"]
