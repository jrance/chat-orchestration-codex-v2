import pytest
from httpx import AsyncClient


def _agent() -> dict:
    return {
        "id": "agent-1",
        "kind": "agent.codeless",
        "label": "A",
        "data": {
            "systemInstructions": "Hello",
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


def _tool() -> dict:
    return {
        "id": "tool-1",
        "kind": "tool",
        "label": "T",
        "data": {"name": "tool:echo", "argsSchema": {"type": "object"}},
    }


@pytest.mark.anyio
async def test_edge_missing_target(async_client: AsyncClient):
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [_agent()],
        "edges": [{"id": "edge-1", "from": "agent-1", "to": "tool-missing"}],
        "entryId": "agent-1",
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is False
    assert any("missing 'to' node 'tool-missing'" in err for err in payload["errors"])


@pytest.mark.anyio
async def test_tool_node_outgoing_forbidden(async_client: AsyncClient):
    pkg = {
        "meta": {"id": "m", "name": "pkg", "version": "1.0.0"},
        "nodes": [_agent(), _tool()],
        "edges": [{"id": "edge-1", "from": "tool-1", "to": "agent-1"}],
        "entryId": "tool-1",
    }

    response = await async_client.post("/v1/validate", json=pkg)

    payload = response.json()
    assert payload["ok"] is False
    assert any(
        "Node 'tool-1' of kind 'tool' must not have outgoing edges" in err
        for err in payload["errors"]
    )
