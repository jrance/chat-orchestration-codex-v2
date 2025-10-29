import pytest
from httpx import AsyncClient


def _agent(node_id: str, label: str) -> dict:
    return {
        "id": node_id,
        "kind": "agent.codeless",
        "label": label,
        "data": {
            "systemInstructions": "Assist.",
            "model": {
                "provider": "openai",
                "modelId": "gpt-4o",
                "temperature": 0.1,
                "topP": 1,
                "maxTokens": 64,
            },
            "context": {"historyWindow": {"mode": "LastN", "n": 1}},
            "tools": {"policy": "Disabled", "attached": []},
        },
    }


@pytest.mark.anyio
async def test_multiple_roots_error(async_client: AsyncClient):
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [_agent("agent-1", "Agent 1"), _agent("agent-2", "Agent 2")],
        "edges": [],
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is False
    assert "multiple root nodes" in " ".join(payload["errors"])


@pytest.mark.anyio
async def test_invalid_entry_reference(async_client: AsyncClient):
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [_agent("agent-1", "Agent 1")],
        "edges": [],
        "entryId": "agent-missing",
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is False
    assert "entryId 'agent-missing' is not a valid node id" in " ".join(payload["errors"])


@pytest.mark.anyio
async def test_infers_entry_when_single_root(async_client: AsyncClient):
    agent = _agent("agent-1", "Root Agent")
    tool = {
        "id": "tool-1",
        "kind": "tool",
        "label": "Tool",
        "data": {"name": "tool:echo", "argsSchema": {"type": "object"}},
    }
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [agent, tool],
        "edges": [{"id": "edge-1", "from": "agent-1", "to": "tool-1"}],
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is True
    assert payload["normalized"]["entryId"] == "agent-1"


@pytest.mark.anyio
async def test_cycle_without_root(async_client: AsyncClient):
    agent_a = _agent("agent-a", "Agent A")
    agent_b = _agent("agent-b", "Agent B")
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [agent_a, agent_b],
        "edges": [
            {"id": "edge-1", "from": "agent-a", "to": "agent-b"},
            {"id": "edge-2", "from": "agent-b", "to": "agent-a"},
        ],
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is False
    assert "no root node could be determined" in " ".join(payload["errors"])
